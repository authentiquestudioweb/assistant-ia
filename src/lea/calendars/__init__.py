from __future__ import annotations

import os

from lea.calendars.base import Calendar, RendezVous
from lea.calendars.mock import MockCalendar
from lea.config import Settings

__all__ = ["Calendar", "RendezVous", "MockCalendar", "make_calendar"]


def make_calendar(settings: Settings, backend: str | None = None) -> Calendar:
    backend = (backend or settings.calendar_backend).lower()
    if backend == "mock":
        return MockCalendar(settings.mock_calendar_path)
    if backend == "google":
        from lea.calendars.google import GoogleCalendar

        return GoogleCalendar(
            credentials_file=settings.google_credentials_file,
            token_file=settings.google_token_file,
            calendar_id=settings.google_calendar_id,
            timezone=settings.timezone,
            service_account_json=os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"),
        )
    raise ValueError(f"CALENDAR_BACKEND inconnu : {backend!r} (mock ou google)")
