"""Horaires d'ouverture et calcul des créneaux libres."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from lea.calendars.base import Calendar


@dataclass(frozen=True)
class Horaires:
    ouverture: int = 8
    fermeture: int = 17
    duree_creneau: int = 30  # minutes
    jours_ouverts: frozenset[int] = field(default_factory=lambda: frozenset({0, 1, 2, 3, 4}))
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Paris"))

    @property
    def duree(self) -> timedelta:
        return timedelta(minutes=self.duree_creneau)

    def est_ouvert(self, d: date) -> bool:
        return d.weekday() in self.jours_ouverts

    def localiser(self, dt: datetime) -> datetime:
        """Naïf -> heure locale du cabinet ; avec fuseau -> converti."""
        return dt.replace(tzinfo=self.tz) if dt.tzinfo is None else dt.astimezone(self.tz)

    def debut_journee(self, d: date) -> datetime:
        return datetime.combine(d, time(self.ouverture), tzinfo=self.tz)

    def fin_journee(self, d: date) -> datetime:
        return datetime.combine(d, time(self.fermeture), tzinfo=self.tz)

    def creneaux_du_jour(self, d: date) -> list[datetime]:
        if not self.est_ouvert(d):
            return []
        slots, cur, fin = [], self.debut_journee(d), self.fin_journee(d)
        while cur + self.duree <= fin:
            slots.append(cur)
            cur += self.duree
        return slots

    def est_creneau_valide(self, dt: datetime) -> bool:
        dt = self.localiser(dt)
        return dt in self.creneaux_du_jour(dt.date())


def _chevauche(debut: datetime, fin: datetime, occupes: list[tuple[datetime, datetime]]) -> bool:
    return any(debut < f and d < fin for d, f in occupes)


def creneaux_libres(
    calendar: Calendar, horaires: Horaires, d: date, now: datetime
) -> list[datetime]:
    slots = horaires.creneaux_du_jour(d)
    if not slots:
        return []
    occupes = calendar.busy_intervals(horaires.debut_journee(d), horaires.fin_journee(d))
    return [
        s for s in slots
        if s > now and not _chevauche(s, s + horaires.duree, occupes)
    ]
