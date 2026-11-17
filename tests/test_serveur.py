"""Page publique : code d'accès, agenda, sérialiseur du mode en ligne."""

import json

import pytest

pytest.importorskip("pipecat")
pytest.importorskip("aiortc")

from fastapi.testclient import TestClient  # noqa: E402

from lea.calendars.base import RendezVous  # noqa: E402
from lea.calendars.mock import MockCalendar  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    from lea.voice import server

    monkeypatch.setenv("MOCK_CALENDAR_PATH", str(tmp_path / "cal.json"))
    monkeypatch.setenv("URGENCES_LOG_PATH", str(tmp_path / "urg.jsonl"))
    monkeypatch.setenv("CALENDAR_BACKEND", "mock")
    return TestClient(server.creer_app(code="12345678")), tmp_path  # sans lifespan : pas de LLM


def test_pages_publiques_mais_donnees_protegees(client):
    c, _ = client
    assert c.get("/").status_code == 200
    assert c.get("/agenda").status_code == 200
    assert c.get("/api/agenda").status_code == 403
    assert c.get("/api/agenda?code=mauvais").status_code == 403
    assert c.get("/api/acces?code=12345678").json()["ok"] is True
    assert c.post("/api/offer", json={"sdp": "x", "type": "offer", "code": "non"}).status_code == 403


def test_trop_d_essais_bloque_meme_le_bon_code(client):
    from lea.voice.server import MAX_ECHECS_CODE

    c, _ = client
    for _ in range(MAX_ECHECS_CODE):
        c.get("/api/acces?code=00000000")
    assert c.get("/api/acces?code=12345678").status_code == 403


def test_websocket_refuse_sans_code(client):
    from starlette.websockets import WebSocketDisconnect

    c, _ = client
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws?code=non"):
            pass


def test_agenda_renvoie_les_rendez_vous_a_venir(client):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    c, tmp = client
    debut = (datetime.now(ZoneInfo("Europe/Paris")) + timedelta(days=2)).replace(microsecond=0)
    MockCalendar(tmp / "cal.json").create_appointment(RendezVous(
        nom="Marie Dupont", telephone="06 12 34 56 78", motif="molaire",
        debut=debut, fin=debut + timedelta(minutes=30), niveau_douleur=7))
    d = c.get("/api/agenda?code=12345678").json()
    assert d["rendez_vous"][0]["nom"] == "Marie Dupont"
    assert d["rendez_vous"][0]["douleur"] == 7




def test_serialiseur_pcm_et_numero_tape():
    import asyncio

    from pipecat.frames.frames import (
        InputAudioRawFrame,
        InputTransportMessageFrame,
        OutputAudioRawFrame,
        OutputTransportMessageUrgentFrame,
    )

    from lea.voice.bot import PCMSerializer

    s = PCMSerializer()
    run = asyncio.run
    assert isinstance(run(s.deserialize(b"\x00\x01")), InputAudioRawFrame)
    m = run(s.deserialize(json.dumps({"type": "telephone", "valeur": "06"})))
    assert isinstance(m, InputTransportMessageFrame) and m.message["valeur"] == "06"
    assert run(s.deserialize("pas du json")) is None
    assert run(s.serialize(OutputAudioRawFrame(audio=b"ab", sample_rate=24000, num_channels=1))) == b"ab"
    txt = run(s.serialize(OutputTransportMessageUrgentFrame(message={"type": "transcript", "text": "é"})))
    assert json.loads(txt)["text"] == "é"


def test_echec_de_transcription_previent_le_patient():
    """Une coupure réseau ne doit jamais laisser l'appel en silence."""
    import asyncio

    from pipecat.frames.frames import InputTransportMessageFrame

    from lea.voice.bot import _classe_groq_stt

    Stt = _classe_groq_stt()
    stt = Stt(api_key="x")

    async def panne(_audio):
        raise TimeoutError("Request timed out.")

    stt._transcribe = panne

    async def lire():
        return [f async for f in stt.run_stt(b"\x00\x00")]

    async def scenario():
        stt.start_processing_metrics = lambda: asyncio.sleep(0)
        stt.stop_processing_metrics = lambda: asyncio.sleep(0)
        return await lire()

    sortie = asyncio.run(scenario())
    assert any(isinstance(f, InputTransportMessageFrame) and f.message["type"] == "stt_echec"
               for f in sortie)
    assert stt._client.timeout.read == 8.0 and stt._client.max_retries == 1
