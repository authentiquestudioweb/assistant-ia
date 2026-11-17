"""Serveur web de Léa : page « Appeler Léa » + signalisation WebRTC, en HTTPS local.

    uv run lea-voice            # Wi-Fi local : ouvrir l'adresse affichée sur le téléphone
    uv run lea-voice --online   # en ligne : adresse publique https + code d'accès

Le micro d'un navigateur n'est autorisé qu'en HTTPS (sauf sur localhost) :
un certificat auto-signé est généré dans data/certs/ pour l'adresse IP du
Mac sur le Wi-Fi. Tout reste sur le réseau local.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import ipaddress
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse

from lea.config import PROJECT_ROOT, load_settings

STATIC = Path(__file__).parent / "static"
MAX_ECHECS_CODE = 20  # mauvais codes tolérés par minute
CERTS = PROJECT_ROOT / "data" / "certs"


def ip_locale() -> str:
    """Adresse du Mac sur le réseau local (aucun paquet n'est envoyé)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def certificat(ip: str) -> tuple[Path, Path]:
    """Crée (ou recrée si l'IP a changé) un certificat auto-signé pour l'IP locale."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    CERTS.mkdir(parents=True, exist_ok=True)
    cert_f, key_f = CERTS / "cert.pem", CERTS / "key.pem"
    if cert_f.exists() and key_f.exists():
        cert = x509.load_pem_x509_certificate(cert_f.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if ipaddress.ip_address(ip) in san.get_values_for_type(x509.IPAddress):
            return cert_f, key_f

    cle = ec.generate_private_key(ec.SECP256R1())
    nom = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Lea (local)")])
    maintenant = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nom).issuer_name(nom)
        .public_key(cle.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(maintenant - dt.timedelta(days=1))
        .not_valid_after(maintenant + dt.timedelta(days=397))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            x509.IPAddress(ipaddress.ip_address(ip)),
        ]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(cle, hashes.SHA256())
    )
    key_f.write_bytes(cle.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    os.chmod(key_f, 0o600)
    cert_f.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_f, key_f


def creer_app(backend: str | None = None, code: str | None = None, max_appels: int = 1):
    from loguru import logger
    from pipecat.transports.smallwebrtc.request_handler import (
        SmallWebRTCRequest,
        SmallWebRTCRequestHandler,
    )

    from lea.cli import build_agent, verifier_llm
    from lea.voice.bot import (
        chemin_modele_whisper,
        lancer_appel,
        lancer_appel_websocket,
        modele_whisper,
    )

    settings = load_settings()
    handler = SmallWebRTCRequestHandler(ice_servers=[])
    appels: set[asyncio.Task] = set()
    en_cours = {"n": 0}  # appels en cours (le CPU n'en supporte bien qu'un)
    app = FastAPI(title="Léa")

    echecs: deque[float] = deque(maxlen=MAX_ECHECS_CODE)

    def code_ok(valeur: str | None) -> bool:
        """Vérifie le code. Trop d'échecs en une minute : tout est bloqué une minute,
        pour rendre inutile d'essayer les codes un par un sur l'adresse publique."""
        if code is None:
            return True
        now = time.monotonic()
        if len(echecs) == MAX_ECHECS_CODE and now - echecs[0] < 60:
            return False
        if secrets.compare_digest(str(valeur or "").encode(), code.encode()):
            return True
        echecs.append(now)
        return False

    def occupe() -> bool:
        return en_cours["n"] >= max_appels

    @app.on_event("startup")
    async def prechauffer():
        # En local : charge Whisper et le LLM en mémoire avant le premier appel.
        if settings.moteur != "groq":
            await asyncio.to_thread(modele_whisper, chemin_modele_whisper())
        try:
            agent = build_agent(settings, backend)
        except RuntimeError as exc:
            logger.error(f"Agenda indisponible : {exc}")
            return
        if not await asyncio.to_thread(verifier_llm, agent, settings):
            logger.error("LLM injoignable : Léa ne pourra pas répondre (voir le README).")
            return
        if settings.moteur != "groq":
            logger.info(f"Préchargement du LLM {settings.llm_model}…")
            await asyncio.to_thread(agent.prechauffer)
        logger.info(f"✅ Léa est prête (moteur {settings.moteur}, modèle {settings.llm_model}).")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/agenda")
    async def page_agenda():
        return FileResponse(STATIC / "agenda.html")

    @app.get("/api/agenda")
    async def api_agenda(code: str | None = None, passes: bool = False):
        if not code_ok(code):
            return JSONResponse({"erreur": "code"}, status_code=403)
        import datetime as _dt
        import json as _json
        from zoneinfo import ZoneInfo

        from lea.calendars import make_calendar
        from lea.dates import format_date_fr, format_heure_fr

        tz = ZoneInfo(settings.timezone)
        maintenant = _dt.datetime.now(tz)
        debut = maintenant - _dt.timedelta(days=30) if passes else maintenant
        try:
            rdvs = await asyncio.to_thread(
                make_calendar(settings, backend).list_appointments, debut, None)
        except NotImplementedError:
            rdvs = []
        sortie = []
        for r in rdvs:
            d = r.debut.astimezone(tz)
            sortie.append({
                "id": r.id, "jour": format_date_fr(d.date()), "date": d.date().isoformat(),
                "heure": format_heure_fr(d), "nom": r.nom, "telephone": r.telephone,
                "motif": r.motif, "depuis_quand": r.depuis_quand,
                "douleur": r.niveau_douleur, "urgence": r.urgence,
                "passe": d < maintenant,
            })
        urgences = []
        if settings.urgences_log_path.exists():
            for ligne in settings.urgences_log_path.read_text(encoding="utf-8").splitlines()[-10:]:
                try:
                    urgences.append(_json.loads(ligne))
                except ValueError:
                    pass
        return {"rendez_vous": sortie, "urgences": urgences[::-1],
                "agenda": settings.calendar_backend}

    @app.get("/api/acces")
    async def acces(code: str | None = None):
        """La page vérifie le code avant d'appeler."""
        if not code_ok(code):
            return JSONResponse({"ok": False, "code_requis": True}, status_code=403)
        return {"ok": True, "occupe": occupe()}

    @app.post("/api/offer")
    async def offer(request: Request):
        donnees = await request.json()
        if not code_ok(donnees.pop("code", None)):
            return JSONResponse({"erreur": "Code d'accès incorrect."}, status_code=403)
        if occupe():
            return JSONResponse({"erreur": "Léa est déjà en ligne."}, status_code=409)

        async def appel_suivi(connexion, agent):
            en_cours["n"] += 1
            try:
                await lancer_appel(connexion, agent)
            finally:
                en_cours["n"] -= 1

        async def demarrer(connexion):
            agent = build_agent(settings, backend)  # un agent (et un historique) par appel
            t = asyncio.create_task(appel_suivi(connexion, agent))
            appels.add(t)
            t.add_done_callback(appels.discard)

        return await handler.handle_web_request(SmallWebRTCRequest.from_dict(donnees), demarrer)

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        """Appel « en ligne » : micro et voix passent par cette WebSocket (donc par
        le tunnel HTTPS), ce qui marche depuis la 4G/5G, contrairement au WebRTC direct."""
        if not code_ok(websocket.query_params.get("code")):
            await websocket.close(code=1008, reason="code")
            return
        if occupe():
            await websocket.close(code=1013, reason="occupe")
            return
        await websocket.accept()
        en_cours["n"] += 1
        try:
            await lancer_appel_websocket(websocket, build_agent(settings, backend))
        finally:
            en_cours["n"] -= 1

    @app.on_event("shutdown")
    async def arreter():
        await handler.close()

    return app


def trouver_cloudflared() -> str | None:
    return shutil.which("cloudflared") or next(
        (str(p) for p in [Path.home() / ".local/bin/cloudflared",
                          Path("/usr/local/bin/cloudflared")] if p.exists()), None)


def lancer_tunnel(port: int, quand_pret) -> subprocess.Popen:
    """Tunnel Cloudflare gratuit et sans compte (« quick tunnel ») :
    https://<mots-au-hasard>.trycloudflare.com -> http://127.0.0.1:<port>."""
    exe = trouver_cloudflared()
    if not exe:
        raise SystemExit("cloudflared introuvable : voir le README (section « En ligne »).")
    proc = subprocess.Popen(
        [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    def lire():
        for ligne in proc.stdout:
            if m := re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", ligne):
                quand_pret(m[0])

    threading.Thread(target=lire, daemon=True).start()
    return proc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lea-voice", description="Léa à la voix (page web)")
    p.add_argument("--port", type=int, default=int(os.environ.get("VOICE_PORT", 7860)))
    p.add_argument("--calendar", choices=["mock", "google"])
    p.add_argument("--tts", choices=["piper", "kokoro"], help="moteur de voix (défaut : piper)")
    p.add_argument("--http", action="store_true",
                   help="HTTP simple (le micro ne marchera que sur http://localhost)")
    p.add_argument("--online", action="store_true",
                   help="adresse publique https (tunnel Cloudflare) protégée par un code")
    p.add_argument("--host", default="0.0.0.0",
                   help="adresse d'écoute (127.0.0.1 = ce Mac uniquement)")
    p.add_argument("--code", default=os.environ.get("LEA_CODE"),
                   help="code d'accès (défaut en ligne : 6 chiffres au hasard)")
    a = p.parse_args(argv)
    load_settings()  # charge .env (LEA_CODE, clés…)
    a.code = a.code or os.environ.get("LEA_CODE")
    if os.environ.get("SPACE_ID") and not a.code:
        raise SystemExit("Refus de démarrer sur une adresse publique sans LEA_CODE (secret du Space).")
    if a.tts:
        os.environ["TTS_ENGINE"] = a.tts
    # Le Python de python.org n'a pas de certificats racine : on utilise ceux de
    # certifi pour les téléchargements automatiques (NLTK, Kokoro, Whisper).
    try:
        import certifi

        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass

    import uvicorn
    from loguru import logger

    logger.remove()
    logger.add(sys.stderr, level=os.environ.get("LOG_LEVEL", "INFO"))

    if a.online:
        return main_en_ligne(a, uvicorn)

    ip = ip_locale()
    ssl = {}
    if not a.http:
        cert_f, key_f = certificat(ip)
        ssl = {"ssl_certfile": str(cert_f), "ssl_keyfile": str(key_f)}
    schema = "http" if a.http else "https"
    print("\n" + "=" * 60, flush=True)
    print("  📞 Léa est en ligne")
    print(f"  Sur ce Mac     : {schema}://localhost:{a.port}")
    print(f"  Sur le téléphone (même Wi-Fi) : {schema}://{ip}:{a.port}")
    if not a.http:
        print("  Certificat auto-signé : acceptez l'avertissement une fois.")
    print("=" * 60 + "\n", flush=True)
    uvicorn.run(creer_app(a.calendar, a.code), host=a.host, port=a.port,
                log_level="warning", **ssl)
    return 0


def main_en_ligne(a, uvicorn) -> int:
    code = a.code or f"{secrets.randbelow(10**6):06d}"
    port = a.port if a.port != 7860 else 7870

    def afficher(url: str):
        print("\n" + "=" * 64, flush=True)
        print("  🌍 Léa est accessible en ligne (4G/5G, n'importe quel Wi-Fi)")
        print(f"  Lien direct  : {url}/?code={code}")
        print(f"  Adresse      : {url}")
        print(f"  Code d'accès : {code}")
        print("  L'adresse change à chaque lancement. Ne la partagez qu'avec")
        print("  les personnes qui doivent tester Léa.")
        print("=" * 64 + "\n", flush=True)

    tunnel = lancer_tunnel(port, afficher)
    try:
        # Écoute uniquement en local : seul le tunnel peut joindre le serveur.
        uvicorn.run(creer_app(a.calendar, code), host="127.0.0.1", port=port,
                    log_level="warning", proxy_headers=True)
    finally:
        tunnel.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
