"""Compréhension des dates et heures exprimées en français.

Toutes les fonctions reçoivent la date du jour explicitement (`today`) pour
rester testables et déterministes.

Convention retenue pour les jours de la semaine : « lundi », « lundi
prochain » et « ce lundi » désignent le prochain lundi *strictement après*
aujourd'hui (si l'on est lundi, c'est donc dans 7 jours). « lundi en huit »
ajoute une semaine de plus. Comme ces expressions restent ambiguës à l'oral,
Léa reformule toujours la date complète avant de confirmer.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time, timedelta


class DateParseError(ValueError):
    """Expression de date ou d'heure non comprise."""


JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = [
    "janvier", "fevrier", "mars", "avril", "mai", "juin",
    "juillet", "aout", "septembre", "octobre", "novembre", "decembre",
]
MOIS_AFFICHAGE = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
NOMBRES = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
    "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "quinze": 15,
}

_JOURS_RE = "|".join(JOURS)
_MOIS_RE = "|".join(MOIS)
_NOMBRE_RE = r"\d+|" + "|".join(NOMBRES)

# « 14h », « 14h30 », « 14 h 30 », « 14:30 », « 9 heures »
_HEURE_RE = re.compile(r"\b(\d{1,2})\s*(?:heures?|h|:)\s*(\d{2})?(?!\d)")


def normaliser(texte: str) -> str:
    """Minuscules, sans accents, apostrophes et espaces uniformisés."""
    t = unicodedata.normalize("NFKD", str(texte))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower().replace("’", "'").replace("`", "'").replace("\u2011", "-").replace("\u2010", "-")
    return re.sub(r"\s+", " ", t).strip()


def _nombre(mot: str) -> int:
    return int(mot) if mot.isdigit() else NOMBRES[mot]


def _date_valide(annee: int, mois: int, jour: int) -> date:
    try:
        return date(annee, mois, jour)
    except ValueError as exc:
        raise DateParseError(f"Date invalide : {jour}/{mois}/{annee}") from exc


def _prochain_jour_semaine(today: date, jour_semaine: int) -> date:
    delta = (jour_semaine - today.weekday()) % 7 or 7
    return today + timedelta(days=delta)


def parse_date(texte: str, today: date) -> date:
    """Convertit une expression française (« demain », « lundi prochain »,
    « le 12 », « 3 décembre », « 2026-11-09 », « 09/11 »…) en date."""
    t = normaliser(texte)
    if not t:
        raise DateParseError("Date vide")
    t = _HEURE_RE.sub(" ", t)
    t = re.sub(r"(?<!apres-)(?<!apres )\bmidi\b", " ", t)  # garde « après-midi »

    # 1. ISO : 2026-11-09
    if m := re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})", t):
        return _date_valide(int(m[1]), int(m[2]), int(m[3]))

    # 2. Numérique : 09/11 ou 09/11/2026
    if m := re.search(r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b", t):
        jour, mois = int(m[1]), int(m[2])
        if m[3]:
            annee = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
            return _date_valide(annee, mois, jour)
        d = _date_valide(today.year, mois, jour)
        return d if d >= today else _date_valide(today.year + 1, mois, jour)

    # 3. Relatifs simples
    if re.search(r"apres[\s-]?demain", t):
        return today + timedelta(days=2)
    if re.search(r"\bdemain\b", t):
        return today + timedelta(days=1)
    if re.search(
        r"aujourd'?\s?hui|\bce jour\b|\bce matin\b|\bcet apres[\s-]?midi\b|"
        r"\bce soir\b|\bmaintenant\b|tout de suite",
        t,
    ):
        return today

    # 4. « dans 3 jours », « dans deux semaines »
    if m := re.search(rf"\bdans (?:les? )?({_NOMBRE_RE}) (jours?|semaines?)\b", t):
        n = _nombre(m[1])
        return today + timedelta(days=n * (7 if m[2].startswith("semaine") else 1))
    if re.search(r"\bdans (?:une|1) semaine\b", t):
        return today + timedelta(days=7)

    # 5. « 12 novembre », « 1er décembre 2027 »
    if m := re.search(rf"\b(\d{{1,2}}|premier)(?:er)?\s+({_MOIS_RE})(?:\s+(\d{{4}}))?\b", t):
        jour = 1 if m[1] == "premier" else int(m[1])
        mois = MOIS.index(m[2]) + 1
        if m[3]:
            return _date_valide(int(m[3]), mois, jour)
        d = _date_valide(today.year, mois, jour)
        return d if d >= today else _date_valide(today.year + 1, mois, jour)

    # 6. Jour de la semaine : « lundi », « lundi prochain », « mardi en huit »,
    #    « mercredi de la semaine prochaine »
    semaine_prochaine = re.search(r"\bsemaine (?:prochaine|qui vient)\b", t)
    if m := re.search(rf"\b({_JOURS_RE})\b", t):
        js = JOURS.index(m[1])
        if semaine_prochaine:
            lundi_prochain = today + timedelta(days=7 - today.weekday())
            return lundi_prochain + timedelta(days=js)
        d = _prochain_jour_semaine(today, js)
        if re.search(r"\ben huit\b", t):
            d += timedelta(days=7)
        return d

    # 7. « la semaine prochaine » seul -> lundi de la semaine prochaine
    if semaine_prochaine:
        return today + timedelta(days=7 - today.weekday())

    # 8. « le 12 », « le premier »
    if m := re.search(r"\ble (\d{1,2}|premier)(?:er)?\b", t):
        jour = 1 if m[1] == "premier" else int(m[1])
        annee, mois = today.year, today.month
        if jour < today.day:
            mois += 1
            if mois == 13:
                annee, mois = annee + 1, 1
        return _date_valide(annee, mois, jour)

    raise DateParseError(f"Date non comprise : « {texte} »")


def parse_time(texte: str) -> time | None:
    """Extrait une heure (« 14h30 », « 9 h », « 10:15 », « midi »). None si absente."""
    t = normaliser(texte)
    if m := _HEURE_RE.search(t):
        heure, minute = int(m[1]), int(m[2] or 0)
        reste = t[m.end():]
        if not m[2]:
            if re.match(r"\s*et demie?\b", reste):
                minute = 30
            elif re.match(r"\s*et quart\b", reste):
                minute = 15
        if heure > 23 or minute > 59:
            raise DateParseError(f"Heure invalide : « {texte} »")
        return time(heure, minute)
    if re.search(r"(?<!apres-)(?<!apres )\bmidi\b", t):  # pas « après-midi »
        return time(12, 30) if re.search(r"\bmidi et demie?\b", t) else time(12, 0)
    return None


def heures_dans(texte: str) -> list[time]:
    """Toutes les heures citées (« 8h, 12h ou 16h30 » -> [8:00, 12:00, 16:30])."""
    heures = []
    for m in _HEURE_RE.finditer(normaliser(texte)):
        h, mn = int(m[1]), int(m[2] or 0)
        if h < 24 and mn < 60:
            heures.append(time(h, mn))
    return heures


_ISO_DT_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2}")


def parse_datetime(texte: str, today: date) -> datetime:
    """« 2026-11-09T09:30 », « 2026-11-09 9:30 », « demain 14h30 »… -> datetime.

    Le résultat peut être naïf (heure locale du cabinet) ou avec fuseau si
    l'entrée ISO en contenait un.
    """
    s = str(texte).strip()
    if _ISO_DT_RE.match(s):
        # « 2026-11-16 9:30 » -> « 2026-11-16T09:30 »
        s = re.sub(r"^(\d{4}-\d{2}-\d{2})[T ](\d):", r"\g<1>T0\2:", s)
        try:
            return datetime.fromisoformat(s.replace(" ", "T", 1))
        except ValueError as exc:
            raise DateParseError(f"Créneau invalide : « {texte} »") from exc
    # ISO abîmé par le modèle, ex. « 2026-11-09TH09:30 » ou « 2026-11-09 à 9h30 »
    if m := re.match(r"^\s*(\d{4}-\d{2}-\d{2})\D+(\d{1,2})[:h](\d{2})?", s, re.I):
        try:
            return datetime.combine(date.fromisoformat(m[1]), time(int(m[2]), int(m[3] or 0)))
        except ValueError as exc:
            raise DateParseError(f"Créneau invalide : « {texte} »") from exc
    d = parse_date(s, today)
    h = parse_time(s)
    if h is None:
        raise DateParseError(f"Heure manquante dans « {texte} »")
    return datetime.combine(d, h)


def format_date_fr(d: date) -> str:
    """date(2026, 11, 9) -> « lundi 9 novembre 2026 »."""
    jour = "1er" if d.day == 1 else str(d.day)
    return f"{JOURS[d.weekday()]} {jour} {MOIS_AFFICHAGE[d.month - 1]} {d.year}"


def format_heure_fr(t: time | datetime) -> str:
    """9:30 -> « 9h30 », 14:00 -> « 14h »."""
    return f"{t.hour}h{t.minute:02d}" if t.minute else f"{t.hour}h"


def format_datetime_fr(dt: datetime) -> str:
    """-> « lundi 9 novembre 2026 à 9h30 »."""
    return f"{format_date_fr(dt.date())} à {format_heure_fr(dt)}"
