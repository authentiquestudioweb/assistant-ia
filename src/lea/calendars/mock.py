"""Agenda local stocké dans un fichier JSON (implémentation par défaut)."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from lea.calendars.base import Calendar, RendezVous


class MockCalendar(Calendar):
    def __init__(self, path: str | os.PathLike = "data/calendar.json"):
        self.path = Path(path)

    # --- persistance -----------------------------------------------------
    def _load(self) -> list[RendezVous]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        return [RendezVous.from_dict(a) for a in data.get("appointments", [])]

    def _save(self, rdvs: list[RendezVous]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"appointments": [r.to_dict() for r in sorted(rdvs, key=lambda r: r.debut)]}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # --- interface Calendar ----------------------------------------------
    def list_appointments(
        self, start: datetime | None = None, end: datetime | None = None
    ) -> list[RendezVous]:
        return sorted(
            (r for r in self._load()
             if (start is None or r.fin > start) and (end is None or r.debut < end)),
            key=lambda r: r.debut,
        )

    def busy_intervals(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        return [(r.debut, r.fin) for r in self._load() if r.debut < end and start < r.fin]

    def create_appointment(self, rdv: RendezVous) -> RendezVous:
        rdvs = self._load()
        if any(rdv.debut < r.fin and r.debut < rdv.fin for r in rdvs):
            raise ValueError("Ce créneau vient d'être pris.")
        rdv.id = rdv.id or uuid.uuid4().hex[:8]
        rdvs.append(rdv)
        self._save(rdvs)
        return rdv
