"""Configuration lue depuis l'environnement / le fichier .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Racine du projet (…/assistant-ia) : .env et data/ y sont cherchés, quel que
# soit le dossier depuis lequel on lance `lea`.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def _chemin(valeur: str) -> Path:
    p = Path(valeur).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p


@dataclass(frozen=True)
class Settings:
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:3b"
    llm_temperature: float = 0.3
    # Modèles de secours essayés si le premier répond « quota atteint » (429)
    llm_fallback_models: tuple[str, ...] = ()
    # "groq" (cloud, rapide) ou "local" (Ollama + faster-whisper sur CPU)
    moteur: str = "local"
    groq_api_key: str = ""

    cabinet_name: str = "Cabinet dentaire du Parc"
    timezone: str = "Europe/Paris"
    opening_hour: int = 8
    closing_hour: int = 17
    slot_minutes: int = 30
    open_days: tuple[int, ...] = (0, 1, 2, 3, 4)

    calendar_backend: str = "mock"
    mock_calendar_path: Path = Path("data/calendar.json")
    urgences_log_path: Path = Path("data/urgences.jsonl")

    google_credentials_file: Path = Path("credentials.json")
    google_token_file: Path = Path("token.json")
    google_calendar_id: str = "primary"


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)
    d = Settings()
    env = os.environ.get

    def _int(name: str, default: int) -> int:
        return int(env(name, str(default)))

    # Moteur : groq dès qu'une clé est présente, sauf MOTEUR=local.
    groq_key = env("GROQ_API_KEY", "").strip()
    moteur = env("MOTEUR", "groq" if groq_key else "local").lower()
    if moteur == "groq":
        llm = dict(
            llm_base_url=GROQ_BASE_URL,
            llm_api_key=groq_key,
            llm_model=env("GROQ_LLM_MODEL", "openai/gpt-oss-120b"),
            llm_fallback_models=tuple(
                m.strip() for m in env("GROQ_LLM_FALLBACK", "openai/gpt-oss-20b,qwen/qwen3.8-27b").split(",")
                if m.strip()),
        )
    else:
        llm = dict(
            llm_base_url=env("LLM_BASE_URL", d.llm_base_url),
            llm_api_key=env("LLM_API_KEY", d.llm_api_key),
            llm_model=env("LLM_MODEL", d.llm_model),
        )

    open_days_raw = env("OPEN_DAYS")
    open_days = (
        tuple(int(x) for x in open_days_raw.split(",") if x.strip())
        if open_days_raw
        else d.open_days
    )

    return Settings(
        **llm,
        moteur=moteur,
        groq_api_key=groq_key,
        llm_temperature=float(env("LLM_TEMPERATURE", str(d.llm_temperature))),
        cabinet_name=env("CABINET_NAME", d.cabinet_name),
        timezone=env("TIMEZONE", d.timezone),
        opening_hour=_int("OPENING_HOUR", d.opening_hour),
        closing_hour=_int("CLOSING_HOUR", d.closing_hour),
        slot_minutes=_int("SLOT_MINUTES", d.slot_minutes),
        open_days=open_days,
        calendar_backend=env("CALENDAR_BACKEND", d.calendar_backend).lower(),
        mock_calendar_path=_chemin(env("MOCK_CALENDAR_PATH", str(d.mock_calendar_path))),
        urgences_log_path=_chemin(env("URGENCES_LOG_PATH", str(d.urgences_log_path))),
        google_credentials_file=_chemin(
            env("GOOGLE_CREDENTIALS_FILE", str(d.google_credentials_file))
        ),
        google_token_file=_chemin(env("GOOGLE_TOKEN_FILE", str(d.google_token_file))),
        google_calendar_id=env("GOOGLE_CALENDAR_ID", d.google_calendar_id),
    )
