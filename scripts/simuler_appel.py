"""Simule un appel téléphonique à Léa, sans micro ni navigateur.

Un « patient » virtuel (voix Piper) dit des phrases, en attendant que Léa ait
fini de répondre avant chaque phrase. On affiche la conversation (reçue via le
canal de données WebRTC) et on enregistre la voix de Léa dans un .wav.

    uv run --extra voice python scripts/simuler_appel.py \
        "Bonjour, j'ai mal à une dent depuis deux jours." "Six sur dix."

Le serveur doit tourner : uv run lea-voice
Mode en ligne (WebSocket, à travers le tunnel) :
    ... simuler_appel.py --ws --url https://xxx.trycloudflare.com --code 123456 "Bonjour"
"""

from __future__ import annotations

import argparse
import asyncio
import fractions
import io
import json
import ssl
import time
import wave
from pathlib import Path

import aiohttp
import av
import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRecorder

RACINE = Path(__file__).resolve().parents[1]
TAUX = 48000


def synthese(texte: str) -> np.ndarray:
    """Phrase -> échantillons int16 mono à 48 kHz (voix Piper)."""
    from piper import PiperVoice

    voix = PiperVoice.load(str(RACINE / "models/piper/fr_FR-siwis-medium.onnx"))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        voix.synthesize_wav(texte, w)
    buf.seek(0)
    with wave.open(buf) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    idx = np.arange(0, len(x), sr / TAUX)
    return np.interp(idx, np.arange(len(x)), x).astype(np.int16)


class Micro(MediaStreamTrack):
    """Piste audio : silence, sauf quand on lui donne une phrase à dire."""

    kind = "audio"

    def __init__(self):
        super().__init__()
        self.file: list[np.ndarray] = []
        self.pts = 0
        self.debut = None
        self.parle = False

    def dire(self, echantillons: np.ndarray):
        self.file.append(echantillons)

    async def recv(self):
        n = TAUX // 50  # trames de 20 ms
        if self.debut is None:
            self.debut = time.time()
        attente = self.debut + self.pts / TAUX - time.time()
        if attente > 0:
            await asyncio.sleep(attente)
        if self.file:
            bloc, self.file[0] = self.file[0][:n], self.file[0][n:]
            if not len(self.file[0]):
                self.file.pop(0)
            bloc = np.pad(bloc, (0, n - len(bloc)))
            self.parle = True
        else:
            bloc = np.zeros(n, dtype=np.int16)
            self.parle = False
        trame = av.AudioFrame.from_ndarray(bloc.reshape(1, -1), format="s16", layout="mono")
        trame.sample_rate = TAUX
        trame.pts = self.pts
        trame.time_base = fractions.Fraction(1, TAUX)
        self.pts += n
        return trame


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("phrases", nargs="+")
    p.add_argument("--url", default="https://localhost:7860")
    p.add_argument("--sortie", default=str(RACINE / "data" / "appel_simule.wav"))
    p.add_argument("--attente-max", type=float, default=90, help="secondes max par réponse")
    p.add_argument("--ws", action="store_true", help="passer par la WebSocket (mode en ligne)")
    p.add_argument("--code", default="")
    a = p.parse_args()
    if a.ws:
        return await appel_websocket(a)

    print("Préparation de la voix du patient…")
    audios = [synthese(ph) for ph in a.phrases]

    pc = RTCPeerConnection()
    micro = Micro()
    pc.addTrack(micro)
    enregistreur = MediaRecorder(a.sortie)
    canal = pc.createDataChannel("chat")
    reponses: asyncio.Queue[str] = asyncio.Queue()

    @canal.on("message")
    def on_message(msg):
        try:
            d = json.loads(msg)
        except (TypeError, ValueError):
            return
        d = d.get("data", d) if isinstance(d, dict) else d
        if isinstance(d, dict) and d.get("type") == "transcript":
            qui = "👤 Patient" if d["role"] == "patient" else "🦷 Léa   "
            print(f"{qui} : {d['text']}")
            if d["role"] == "lea":
                reponses.put_nowait(d["text"])

    @pc.on("track")
    def on_track(track):
        enregistreur.addTrack(track)

    await pc.setLocalDescription(await pc.createOffer())
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # certificat auto-signé local
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{a.url}/api/offer", ssl=ctx, json={
            "sdp": pc.localDescription.sdp, "type": pc.localDescription.type}) as r:
            r.raise_for_status()
            rep = await r.json()
    await pc.setRemoteDescription(RTCSessionDescription(sdp=rep["sdp"], type=rep["type"]))
    await enregistreur.start()

    t0 = time.time()
    try:
        # ~13 caractères par seconde à l'oral : on laisse Léa finir de parler.
        def duree_parole(texte: str) -> float:
            return len(texte) / 13 + 2.5

        salut = await asyncio.wait_for(reponses.get(), 30)
        await asyncio.sleep(duree_parole(salut))
        for audio in audios:
            micro.dire(audio)
            t = time.time()
            try:
                texte = await asyncio.wait_for(reponses.get(), a.attente_max)
                print(f"   (réponse affichée {time.time() - t:.1f} s après le début de la phrase)")
                await asyncio.sleep(duree_parole(texte))
            except asyncio.TimeoutError:
                print(f"   ⚠️ pas de réponse en {a.attente_max:.0f} s")
    finally:
        await enregistreur.stop()
        await pc.close()
    print(f"\nDurée totale : {time.time() - t0:.0f} s — voix de Léa : {a.sortie}")


async def appel_websocket(a):
    """Même scénario, mais par la WebSocket /ws (PCM 16 kHz montant, 24 kHz descendant)."""
    import websockets

    print("Préparation de la voix du patient…")
    phrases = []
    for ph in a.phrases:
        if ph.startswith("#tel:"):  # numéro « tapé » avec le bouton de la page
            phrases.append(ph[5:].strip())
            continue
        x = synthese(ph)  # 48 kHz
        phrases.append(x[::3].copy())  # -> 16 kHz
    url = a.url.replace("https://", "wss://").replace("http://", "ws://") + f"/ws?code={a.code}"
    import certifi

    ctx = ssl.create_default_context(cafile=certifi.where())
    reponses: asyncio.Queue[str] = asyncio.Queue()
    recu = bytearray()
    a_dire: list[np.ndarray] = []
    t0 = time.time()
    async with websockets.connect(url, ssl=ctx if url.startswith("wss") else None,
                                  max_size=None) as ws:
        async def envoyer():
            n, t = 320, time.time()
            while True:
                if a_dire:
                    bloc, a_dire[0] = a_dire[0][:n], a_dire[0][n:]
                    if not len(a_dire[0]):
                        a_dire.pop(0)
                    bloc = np.pad(bloc, (0, n - len(bloc)))
                else:
                    bloc = np.zeros(n, dtype=np.int16)
                await ws.send(bloc.astype(np.int16).tobytes())
                t += 0.02
                await asyncio.sleep(max(0, t - time.time()))

        async def recevoir():
            async for msg in ws:
                if isinstance(msg, bytes):
                    recu.extend(msg)
                    continue
                d = json.loads(msg)
                if d.get("type") == "transcript":
                    qui = "👤 Patient" if d["role"] == "patient" else "🦷 Léa   "
                    print(f"{qui} : {d['text']}")
                    if d["role"] == "lea":
                        reponses.put_nowait(d["text"])

        taches = [asyncio.create_task(envoyer()), asyncio.create_task(recevoir())]
        try:
            salut = await asyncio.wait_for(reponses.get(), 40)
            await asyncio.sleep(len(salut) / 13 + 2.5)
            for audio in phrases:
                if isinstance(audio, str):
                    await ws.send(json.dumps({"type": "telephone", "valeur": audio}))
                else:
                    a_dire.append(audio)
                t = time.time()
                try:
                    texte = await asyncio.wait_for(reponses.get(), a.attente_max)
                    print(f"   (réponse affichée {time.time() - t:.1f} s après le début de la phrase)")
                    await asyncio.sleep(len(texte) / 13 + 2.5)
                except asyncio.TimeoutError:
                    print(f"   ⚠️ pas de réponse en {a.attente_max:.0f} s")
        finally:
            for t in taches:
                t.cancel()
    with wave.open(a.sortie, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(bytes(recu))
    print(f"\nDurée : {time.time() - t0:.0f} s — {len(recu) / 48000:.1f} s de voix reçue -> {a.sortie}")


if __name__ == "__main__":
    asyncio.run(main())
