"""Crée ou met à jour le Space Hugging Face de Léa.

    uv run --extra voice python scripts/deployer_space.py --space VOTRE_NOM/lea

Prérequis (voir le README) : `hf auth login`, GROQ_API_KEY dans .env, et pour
un agenda durable : service-account.json + GOOGLE_CALENDAR_ID dans .env.

Le code source part dans le Space (public) via une LISTE BLANCHE : ni .env, ni
compte de service, ni data/ ne peuvent être envoyés. Les clés sont transmises
uniquement comme secrets du Space.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values
from huggingface_hub import HfApi
from huggingface_hub.utils import filter_repo_objects

RACINE = Path(__file__).resolve().parents[1]
FICHIERS = ["Dockerfile", ".dockerignore", ".python-version", "README.md",
            "pyproject.toml", "uv.lock", "src/**"]
EXCLUS = ["**/__pycache__/**", "*.pyc"]
VARIABLES = ["CABINET_NAME", "TIMEZONE", "OPENING_HOUR", "CLOSING_HOUR", "SLOT_MINUTES", "OPEN_DAYS"]


def fichiers_a_envoyer() -> list[str]:
    tous = [str(p.relative_to(RACINE)) for p in RACINE.rglob("*")
            if p.is_file() and ".venv" not in p.parts]
    return sorted(filter_repo_objects(tous, allow_patterns=FICHIERS, ignore_patterns=EXCLUS))


def adresse(space: str) -> str:
    return "https://" + re.sub(r"[^a-z0-9-]", "-", space.lower().replace("/", "-")) + ".hf.space"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--space", required=True, help="VOTRE_NOM/lea")
    p.add_argument("--essai", action="store_true", help="affiche ce qui serait envoyé, sans rien envoyer")
    a = p.parse_args()

    fichier_env = RACINE / ".env"
    env = dotenv_values(fichier_env)
    fichiers = fichiers_a_envoyer()
    compte_service = RACINE / "service-account.json"
    if compte_service.exists() and env.get("GOOGLE_CALENDAR_ID", "primary") == "primary":
        sys.exit("Mettez GOOGLE_CALENDAR_ID=votre.adresse@gmail.com dans .env (pas « primary »).")
    agenda_google = compte_service.exists()

    if a.essai:
        print("Fichiers qui seraient envoyés :", *fichiers, sep="\n  ")
        print(f"Agenda : {'Google' if agenda_google else 'fichier local (effacé à chaque redémarrage)'}")
        return 0

    if not env.get("GROQ_API_KEY"):
        sys.exit("GROQ_API_KEY manquant dans .env (voir le README).")

    code = env.get("LEA_CODE")
    if not code:
        code = f"{secrets.randbelow(10**8):08d}"
        with fichier_env.open("a", encoding="utf-8") as f:
            f.write(f"\nLEA_CODE={code}\n")

    api = HfApi()
    api.whoami()  # échoue clairement si `hf auth login` n'a pas été fait
    api.create_repo(a.space, repo_type="space", space_sdk="docker", exist_ok=True)

    # Secrets et variables avant l'envoi, pour que le premier démarrage les ait.
    api.add_space_secret(a.space, "GROQ_API_KEY", env["GROQ_API_KEY"])
    api.add_space_secret(a.space, "LEA_CODE", code)
    if agenda_google:
        api.add_space_secret(a.space, "GOOGLE_SERVICE_ACCOUNT_JSON",
                             compte_service.read_text(encoding="utf-8"))
        api.add_space_variable(a.space, "GOOGLE_CALENDAR_ID", env["GOOGLE_CALENDAR_ID"])
    api.add_space_variable(a.space, "CALENDAR_BACKEND", "google" if agenda_google else "mock")
    for nom in VARIABLES:
        if env.get(nom):
            api.add_space_variable(a.space, nom, env[nom])

    api.upload_folder(repo_id=a.space, repo_type="space", folder_path=str(RACINE),
                      allow_patterns=FICHIERS, ignore_patterns=EXCLUS,
                      commit_message="Déploiement de Léa")

    url = adresse(a.space)
    print(f"\n✅ Envoyé. Construction en cours (5 à 10 min) : https://huggingface.co/spaces/{a.space}")
    print(f"   Adresse : {url}\n   Lien direct : {url}/?code={code}\n   Agenda : {url}/agenda")
    if not agenda_google:
        print("   ⚠️ Sans compte de service Google, l'agenda est effacé à chaque redémarrage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
