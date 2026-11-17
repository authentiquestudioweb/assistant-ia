"""Agenda Google Calendar (OAuth « application de bureau »).

Dépendances facultatives : `uv sync --extra google`.
Première autorisation : `uv run lea-google-auth` (ouvre le navigateur).
Le motif et les infos patient sont écrits dans la description de l'événement.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from lea.calendars.base import Calendar, RendezVous

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def _import_google():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - dépend de l'extra
        raise RuntimeError(
            "Dépendances Google absentes. Lancez : uv sync --extra google"
        ) from exc
    return Request, Credentials, InstalledAppFlow, build


def get_credentials(credentials_file: Path, token_file: Path, interactive: bool = True):
    Request, Credentials, InstalledAppFlow, _ = _import_google()
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not interactive:
            raise RuntimeError(
                "Google Calendar non autorisé. Lancez d'abord : uv run lea-google-auth"
            )
        if not credentials_file.exists():
            raise RuntimeError(
                f"Fichier {credentials_file} introuvable (voir README, section Google OAuth)."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
        creds = flow.run_local_server(port=0)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(token_file, 0o600)
    return creds


def credentials_compte_de_service(contenu_json: str):
    """Compte de service : idéal sur un serveur, pas de connexion qui expire.
    L'agenda doit être partagé avec l'adresse e-mail du compte de service."""
    try:
        from google.oauth2.service_account import Credentials as ServiceCredentials
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Dépendances Google absentes : uv sync --extra google") from exc
    try:
        infos = json.loads(contenu_json)
    except ValueError as exc:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON n'est pas un JSON valide.") from exc
    return ServiceCredentials.from_service_account_info(infos, scopes=SCOPES)


class GoogleCalendar(Calendar):
    def __init__(
        self,
        credentials_file: str | os.PathLike = "credentials.json",
        token_file: str | os.PathLike = "token.json",
        calendar_id: str = "primary",
        timezone: str = "Europe/Paris",
        service=None,
        service_account_json: str | None = None,
    ):
        self.calendar_id = calendar_id
        self.tz = ZoneInfo(timezone)
        self.timezone = timezone
        if service is None:
            *_, build = _import_google()
            if service_account_json:
                if calendar_id == "primary":
                    raise RuntimeError(
                        "Avec un compte de service, GOOGLE_CALENDAR_ID doit être l'identifiant "
                        "de votre agenda partagé (souvent votre adresse Gmail), pas « primary »."
                    )
                creds = credentials_compte_de_service(service_account_json)
            else:
                creds = get_credentials(Path(credentials_file), Path(token_file), interactive=False)
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self.service = service

    def _parse(self, when: dict, fin: bool = False) -> datetime:
        if "dateTime" in when:
            return datetime.fromisoformat(when["dateTime"].replace("Z", "+00:00"))
        # Événement « journée entière » : date de fin exclusive
        d = datetime.fromisoformat(when["date"]).date()
        return datetime.combine(d, time.min, tzinfo=self.tz)

    def busy_intervals(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        occupes, token = [], None
        while True:
            resp = (
                self.service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=start.isoformat(),
                    timeMax=end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=token,
                )
                .execute()
            )
            for ev in resp.get("items", []):
                if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
                    continue
                occupes.append((self._parse(ev["start"]), self._parse(ev["end"], fin=True)))
            token = resp.get("nextPageToken")
            if not token:
                return occupes

    def list_appointments(
        self, start: datetime | None = None, end: datetime | None = None
    ) -> list[RendezVous]:
        """Événements créés par Léa (repérés par « Pris par Léa » dans la description)."""
        params = dict(calendarId=self.calendar_id, singleEvents=True, orderBy="startTime",
                      q="Pris par Léa")
        if start:
            params["timeMin"] = start.isoformat()
        if end:
            params["timeMax"] = end.isoformat()
        rdvs, token = [], None
        while True:
            resp = self.service.events().list(pageToken=token, **params).execute()
            for ev in resp.get("items", []):
                desc = ev.get("description", "")
                champ = lambda nom: next((l.split(":", 1)[1].strip() for l in desc.splitlines()
                                          if l.startswith(nom)), None)  # noqa: E731
                douleur = champ("Douleur")
                rdvs.append(RendezVous(
                    nom=champ("Patient") or ev.get("summary", ""),
                    telephone=champ("Téléphone") or "",
                    motif=champ("Motif") or "",
                    debut=self._parse(ev["start"]), fin=self._parse(ev["end"]),
                    depuis_quand=champ("Depuis"),
                    niveau_douleur=int(douleur.split("/")[0]) if douleur else None,
                    urgence="URGENCE" in desc, id=ev.get("id"),
                ))
            token = resp.get("nextPageToken")
            if not token:
                return rdvs

    def create_appointment(self, rdv: RendezVous) -> RendezVous:
        prefixe = "🚨 " if rdv.urgence else ""
        body = {
            "summary": f"{prefixe}RDV {rdv.nom} — {rdv.motif}",
            "description": rdv.description(),
            "start": {"dateTime": rdv.debut.isoformat(), "timeZone": self.timezone},
            "end": {"dateTime": rdv.fin.isoformat(), "timeZone": self.timezone},
        }
        ev = self.service.events().insert(calendarId=self.calendar_id, body=body).execute()
        rdv.id = ev.get("id")
        return rdv


def auth_main() -> None:
    """Commande `lea-google-auth` : autorise l'accès à Google Calendar."""
    from lea.config import load_settings

    s = load_settings()
    get_credentials(s.google_credentials_file, s.google_token_file, interactive=True)
    print(f"✅ Autorisation enregistrée dans {s.google_token_file}")
    print("   Mettez CALENDAR_BACKEND=google dans .env pour l'utiliser.")
