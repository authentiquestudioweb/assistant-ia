"""Pipeline vocal d'un appel.

    micro (WebRTC) -> Silero VAD -> faster-whisper (fr) -> Léa (agent + garde-fous)
                   -> Piper (fr) ou Kokoro -> haut-parleur (WebRTC)

Léa réutilise exactement l'agent texte (LeaAgent) : mêmes outils, mêmes
garde-fous. Le LLM tourne dans un thread pour ne pas bloquer l'audio.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from functools import lru_cache
from pathlib import Path

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    InputAudioRawFrame,
    InputTransportMessageFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    Frame,
    InterimTranscriptionFrame,
    OutputTransportMessageUrgentFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from lea.agent import LeaAgent
from lea.config import PROJECT_ROOT, load_settings
from lea.voice.speech import pour_la_voix, transcription_utile

STT_TIMEOUT_SECS = 8.0
MESSAGE_PAS_ENTENDU = "Excusez-moi, je n'ai pas bien entendu. Pouvez-vous répéter, s'il vous plaît ?"
PATIENCE = [
    "Un instant, s'il vous plaît.",
    "Je regarde ça.",
    "Je vérifie, un petit instant.",
]


def _env(nom: str, defaut: str) -> str:
    return os.environ.get(nom, defaut)


def chemin_modele_whisper() -> str:
    """Dossier local models/whisper-small s'il existe, sinon nom du modèle
    (téléchargé automatiquement depuis Hugging Face au premier lancement)."""
    v = _env("WHISPER_MODEL", "models/whisper-small")
    p = Path(v) if Path(v).is_absolute() else PROJECT_ROOT / v
    return str(p) if (p / "model.bin").exists() else (Path(v).name.removeprefix("whisper-") or "small")


# Vocabulaire soufflé à Whisper (sans chiffres, pour ne pas biaiser les numéros).
VOCABULAIRE = (
    "Appel au cabinet dentaire. Rendez-vous avec le dentiste. J'ai mal à une dent, "
    "une molaire, une dent de sagesse, une incisive. Carie, détartrage, couronne, "
    "plombage, gencive, abcès, joue gonflée, fièvre, saignement. Lundi, mardi, mercredi, "
    "jeudi, vendredi, demain, la semaine prochaine, le matin, l'après-midi."
)


class _WhisperVocabulaire:
    """Enveloppe le modèle pour ajouter `initial_prompt` à chaque transcription."""

    def __init__(self, modele, prompt: str):
        self._modele, self._prompt = modele, prompt

    def transcribe(self, audio, **kwargs):
        kwargs.setdefault("initial_prompt", self._prompt)
        return self._modele.transcribe(audio, **kwargs)

    def __getattr__(self, nom):
        return getattr(self._modele, nom)


@lru_cache(maxsize=1)
def modele_whisper(chemin: str):
    """Chargé une seule fois pour tous les appels (quelques secondes sur CPU)."""
    from faster_whisper import WhisperModel

    logger.info(f"Chargement de Whisper « {chemin} » (CPU, int8)…")
    return _WhisperVocabulaire(WhisperModel(chemin, device="cpu", compute_type="int8"), VOCABULAIRE)


class WhisperCPU(WhisperSTTService):
    """WhisperSTTService qui réutilise le modèle déjà chargé en mémoire."""

    def _load(self):  # noqa: D401 - surcharge
        self._model = modele_whisper(self._settings.model)


def _classe_groq_stt():
    import httpx
    from openai import AsyncOpenAI
    from pipecat.services.groq.stt import GroqSTTService

    class GroqSTTFiable(GroqSTTService):
        """Le client par défaut attend jusqu'à 10 minutes : une seule connexion réseau
        bloquée figeait tout l'appel. Ici : délai court, une nouvelle tentative, une
        connexion neuve par phrase, et le patient est prévenu en cas d'échec."""

        def _create_client(self, api_key, base_url):
            return AsyncOpenAI(
                api_key=api_key, base_url=base_url, max_retries=1,
                timeout=httpx.Timeout(STT_TIMEOUT_SECS, connect=3.0),
                http_client=httpx.AsyncClient(
                    limits=httpx.Limits(max_keepalive_connections=0)),
            )

        async def run_stt(self, audio):
            async for frame in super().run_stt(audio):
                if isinstance(frame, ErrorFrame):
                    logger.warning(f"Transcription impossible : {frame.error}")
                    yield InputTransportMessageFrame(message={"type": "stt_echec"})
                else:
                    yield frame

    return GroqSTTFiable


def creer_stt():
    settings = load_settings()
    if settings.moteur == "groq":
        GroqSTTService = _classe_groq_stt()

        return GroqSTTService(
            api_key=settings.groq_api_key,
            settings=GroqSTTService.Settings(
                model=_env("GROQ_STT_MODEL", "whisper-large-v3"),
                language=Language.FR,
                prompt=VOCABULAIRE,
            ),
        )
    return WhisperCPU(
        device="cpu",
        compute_type="int8",
        settings=WhisperSTTService.Settings(
            model=chemin_modele_whisper(), language=Language.FR, no_speech_prob=0.5
        ),
    )


def creer_tts():
    moteur = _env("TTS_ENGINE", "piper").lower()
    if moteur == "kokoro":
        from pipecat.services.kokoro.tts import KokoroTTSService

        logger.info("Voix : Kokoro (ff_siwis)")
        return KokoroTTSService(
            settings=KokoroTTSService.Settings(
                voice=_env("KOKORO_VOICE", "ff_siwis"), language=Language.FR
            )
        )
    from pipecat.services.piper.tts import PiperTTSService

    voix = _env("PIPER_VOICE", "fr_FR-siwis-medium")
    logger.info(f"Voix : Piper ({voix})")
    return PiperTTSService(
        settings=PiperTTSService.Settings(voice=voix),
        download_dir=PROJECT_ROOT / "models" / "piper",
    )


class LeaProcessor(FrameProcessor):
    """Transforme chaque phrase du patient en réponse parlée de Léa."""

    def __init__(self, agent: LeaAgent, patience_secs: float = 3.5, **kwargs):
        super().__init__(**kwargs)
        self.agent = agent
        self.patience_secs = patience_secs
        self._bot_parle = False
        self._fin_parole = 0.0
        self._en_attente: list[str] = []
        self._occupe = False
        self.raccroche = False

    async def message_page(self, role: str, texte: str) -> None:
        """Affiche la conversation sur la page web (canal de données WebRTC)."""
        await self.push_frame(OutputTransportMessageUrgentFrame(
            message={"type": "transcript", "role": role, "text": texte}))

    async def dire(self, texte: str) -> None:
        await self.message_page("lea", texte)
        await self.push_frame(TTSSpeakFrame(pour_la_voix(texte)))

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_parle = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_parle = False
            self._fin_parole = time.monotonic()

        if isinstance(frame, TranscriptionFrame):
            texte = frame.text.strip()
            # Pendant que Léa parle, on ignore le micro (évite qu'elle s'entende
            # elle-même si l'annulation d'écho du téléphone laisse passer sa voix).
            if self._bot_parle or time.monotonic() - self._fin_parole < 0.2:
                logger.debug(f"Ignoré (Léa parlait) : {texte!r}")
            elif self.raccroche:
                pass
            elif transcription_utile(texte):
                logger.info(f"Patient : {texte}")
                await self.message_page("patient", texte)
                self._en_attente.append(texte)
                if not self._occupe:
                    self.create_task(self._traiter())
            return
        if isinstance(frame, InterimTranscriptionFrame):
            return
        if isinstance(frame, InputTransportMessageFrame):
            msg = frame.message if isinstance(frame.message, dict) else {}
            if msg.get("type") == "stt_echec" and not self.raccroche and not self._occupe:
                await self.dire(MESSAGE_PAS_ENTENDU)
                return
            if msg.get("type") == "telephone" and not self.raccroche:
                # Numéro tapé sur la page : fiable, sans passer par Whisper.
                numero = self.agent.saisir_telephone(str(msg.get("valeur", "")))
                if not numero:
                    await self.dire("Ce numéro semble incomplet. Pouvez-vous le retaper en entier ?")
                    return
                texte = f"Mon numéro de téléphone est le {numero}"
                logger.info(f"Patient (clavier) : {texte}")
                await self.message_page("patient", texte)
                self._en_attente.append(texte)
                if not self._occupe:
                    self.create_task(self._traiter())
            return

        await self.push_frame(frame, direction)

    async def _patienter(self):
        await asyncio.sleep(self.patience_secs)
        await self.push_frame(TTSSpeakFrame(random.choice(PATIENCE)))

    async def _traiter(self):
        self._occupe = True
        try:
            while self._en_attente:
                texte = " ".join(self._en_attente)
                self._en_attente.clear()
                attente = self.create_task(self._patienter())
                try:
                    reponse = await asyncio.to_thread(self.agent.respond, texte)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(f"Erreur LLM : {exc}")
                    reponse = "Excusez-moi, j'ai un petit souci technique. Pouvez-vous répéter ?"
                finally:
                    await self.cancel_task(attente)
                if self.raccroche:
                    logger.info("Réponse abandonnée : le patient a raccroché")
                    return
                logger.info(f"Léa : {reponse}")
                await self.dire(reponse)
        finally:
            self._occupe = False


SAMPLE_RATE_SORTIE = 24000  # la page web joue l'audio à ce débit


async def executer_appel(transport, agent: LeaAgent) -> None:
    """Chaîne commune aux deux transports (WebRTC local, WebSocket en ligne)."""
    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(
            params=VADParams(stop_secs=float(_env("VAD_STOP_SECS", "0.8")))
        )
    )
    lea = LeaProcessor(agent)
    pipeline = Pipeline([transport.input(), vad, creer_stt(), lea, creer_tts(), transport.output()])
    task = PipelineTask(pipeline, params=PipelineParams(
        allow_interruptions=False, audio_out_sample_rate=SAMPLE_RATE_SORTIE))

    @transport.event_handler("on_client_connected")
    async def on_connected(_transport, _client):
        logger.info("Appel connecté")
        salut = agent.greeting()
        await task.queue_frames([
            OutputTransportMessageUrgentFrame(
                message={"type": "transcript", "role": "lea", "text": salut}),
            TTSSpeakFrame(pour_la_voix(salut)),
        ])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(_transport, _client):
        logger.info("Appel terminé")
        lea.raccroche = True
        await task.cancel()

    await PipelineRunner(handle_sigint=False).run(task)


async def lancer_appel(connexion: SmallWebRTCConnection, agent: LeaAgent) -> None:
    """Appel WebRTC (réseau local)."""
    transport = SmallWebRTCTransport(
        webrtc_connection=connexion,
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    )
    await executer_appel(transport, agent)


class PCMSerializer(FrameSerializer):
    """Protocole minimal pour la page web en mode « en ligne » :

    - binaire reçu  = micro, PCM 16 bits mono 16 kHz ;
    - binaire envoyé = voix de Léa, PCM 16 bits mono 24 kHz ;
    - texte envoyé  = messages JSON (transcription affichée sur la page).
    """

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            if self.should_ignore_frame(frame):
                return None
            return json.dumps(frame.message, ensure_ascii=False)
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, (bytes, bytearray)) and data:
            return InputAudioRawFrame(audio=bytes(data), sample_rate=16000, num_channels=1)
        if isinstance(data, str):
            try:
                return InputTransportMessageFrame(message=json.loads(data))
            except ValueError:
                return None
        return None


async def lancer_appel_websocket(websocket, agent: LeaAgent) -> None:
    """Appel par WebSocket : tout passe par HTTPS, donc par le tunnel public."""
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketParams,
        FastAPIWebsocketTransport,
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=PCMSerializer(),
        ),
    )
    await executer_appel(transport, agent)
