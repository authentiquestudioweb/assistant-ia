"""Mise en forme du texte avant synthèse vocale, et filtrage des transcriptions."""

from __future__ import annotations

import re

_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def pour_la_voix(texte: str) -> str:
    """Rend un texte agréable à prononcer pour Piper/Kokoro.

    « 9h30 » -> « 9 heures 30 », « 6/10 » -> « 6 sur 10 », « 1er » -> « premier »…
    """
    t = _EMOJI_RE.sub("", texte or "")
    t = re.sub(r"[*_`#]", "", t)
    t = re.sub(r"\b(\d{1,2})\s?h(?:\s?(\d{2}))?\b", _heure_simple, t)
    t = re.sub(r"\b(\d{1,2})\s?/\s?10\b", r"\1 sur 10", t)
    t = re.sub(r"\b1er\b", "premier", t)
    # À l'oral, l'année alourdit : « jeudi 12 novembre 2026 » -> « jeudi 12 novembre »
    t = re.sub(r"(\b(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre"
               r"|novembre|décembre))\s+20\d\d\b", r"\1", t)
    t = re.sub(r"\bRDV\b", "rendez-vous", t, flags=re.I)
    t = re.sub(r"\bDr\.?\s", "docteur ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _heure_simple(m: re.Match) -> str:
    h, mn = int(m[1]), m[2]
    mot = "heure" if h <= 1 else "heures"
    if mn and int(mn):
        return f"{h} {mot} {int(mn)}"
    return f"{h} {mot}"


# Phrases que Whisper « entend » parfois dans le silence ou le bruit (hallucinations).
_HALLUCINATIONS = re.compile(
    r"sous-titr|amara\.org|merci d'avoir regard|abonnez-vous|^\W*(merci|musique|rires?)\W*$",
    re.I,
)


def transcription_utile(texte: str) -> bool:
    t = (texte or "").strip()
    if len(re.sub(r"\W", "", t)) < 2:
        return False
    return not _HALLUCINATIONS.search(t)
