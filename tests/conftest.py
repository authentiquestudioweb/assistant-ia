from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from lea.calendars.mock import MockCalendar
from lea.scheduling import Horaires
from lea.tools import LeaTools

PARIS = ZoneInfo("Europe/Paris")


@pytest.fixture
def horaires():
    return Horaires(tz=PARIS)


@pytest.fixture
def calendar(tmp_path):
    return MockCalendar(tmp_path / "calendar.json")


def make_tools(calendar, horaires, now: datetime, tmp_path=None):
    return LeaTools(
        calendar,
        horaires,
        now_fn=lambda: now,
        urgences_path=(tmp_path / "urgences.jsonl") if tmp_path else None,
    )


@pytest.fixture
def tools(calendar, horaires, tmp_path):
    # Mercredi 11 novembre 2026, 10h00 heure de Paris
    return make_tools(calendar, horaires, datetime(2026, 11, 11, 10, 0, tzinfo=PARIS), tmp_path)
