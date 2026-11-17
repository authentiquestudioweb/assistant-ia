"""Agent texte en ligne de commande : `uv run lea`."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from zoneinfo import ZoneInfo

from lea.agent import LeaAgent
from lea.calendars import make_calendar
from lea.config import Settings, load_settings
from lea.scheduling import Horaires
from lea.tools import LeaTools

try:  # édition de ligne (flèches, historique)
    import readline  # noqa: F401
except ImportError:  # pragma: no cover
    pass

ROUGE, GRIS, BLEU, RAZ = "\033[31m", "\033[90m", "\033[36m", "\033[0m"


def horaires_depuis(s: Settings) -> Horaires:
    return Horaires(
        ouverture=s.opening_hour,
        fermeture=s.closing_hour,
        duree_creneau=s.slot_minutes,
        jours_ouverts=frozenset(s.open_days),
        tz=ZoneInfo(s.timezone),
    )


def build_agent(s: Settings, backend: str | None = None, debug: bool = False) -> LeaAgent:
    from openai import OpenAI

    def on_urgence(evt: dict) -> None:
        print(f"{ROUGE}⚠️  URGENCE signalée à l'équipe : {evt['raison'] or '(sans détail)'}{RAZ}",
              file=sys.stderr)

    def on_tool_call(nom: str, args: str, res: dict) -> None:
        if debug:
            print(f"{GRIS}  ↳ {nom}({args}) -> {res}{RAZ}", file=sys.stderr)

    tools = LeaTools(
        calendar=make_calendar(s, backend),
        horaires=horaires_depuis(s),
        urgences_path=s.urgences_log_path,
        on_urgence=on_urgence,
    )
    # En cloud : pas de nouvelle tentative lente, on bascule tout de suite de modèle.
    cloud = s.moteur == "groq"
    client = OpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key,
                    timeout=8 if cloud else 180, max_retries=0 if cloud else 2)
    return LeaAgent(
        client, s.llm_model, tools,
        cabinet=s.cabinet_name, temperature=s.llm_temperature, on_tool_call=on_tool_call,
        modeles_secours=s.llm_fallback_models,
        # Les modèles cloud comptent leur réflexion dans la limite : 150 la couperait.
        max_tokens=500 if cloud else 150,
    )


def verifier_llm(agent: LeaAgent, s: Settings) -> bool:
    try:
        modeles = {m.id for m in agent.client.models.list()}
    except Exception as exc:  # noqa: BLE001
        print(f"{ROUGE}Impossible de joindre le LLM sur {s.llm_base_url} ({exc.__class__.__name__}).{RAZ}")
        if "11434" in s.llm_base_url:
            print("Ollama est-il lancé ?  ->  ollama serve   (ou ouvrez l'app Ollama)")
        return False
    if modeles and s.llm_model not in modeles:
        print(f"{ROUGE}Modèle « {s.llm_model} » introuvable.{RAZ}  ->  ollama pull {s.llm_model}")
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lea", description="Léa, réceptionniste IA (mode texte)")
    p.add_argument("--calendar", choices=["mock", "google"], help="agenda (défaut : .env)")
    p.add_argument("--model", help="modèle LLM (défaut : .env)")
    p.add_argument("--debug", action="store_true", help="affiche les appels d'outils")
    a = p.parse_args(argv)

    s = load_settings()
    if a.model:
        s = dataclasses.replace(s, llm_model=a.model)
    try:
        agent = build_agent(s, a.calendar, a.debug)
    except RuntimeError as exc:
        print(f"{ROUGE}{exc}{RAZ}")
        return 1
    if not verifier_llm(agent, s):
        return 1

    print(f"{GRIS}Léa — modèle {s.llm_model}, agenda {a.calendar or s.calendar_backend}. "
          f"Commandes : /reset (nouvel appel), /quit{RAZ}\n")
    print(f"{BLEU}Léa :{RAZ} {agent.greeting()}")
    while True:
        try:
            texte = input("\nVous : ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAu revoir !")
            return 0
        if not texte:
            continue
        if texte in {"/quit", "/q", "/exit"}:
            print("Au revoir !")
            return 0
        if texte == "/reset":
            agent.reset()
            print(f"{GRIS}— nouvel appel —{RAZ}\n{BLEU}Léa :{RAZ} {agent.greeting()}")
            continue
        try:
            reponse = agent.respond(texte)
        except Exception as exc:  # noqa: BLE001
            print(f"{ROUGE}Erreur LLM : {exc}{RAZ}")
            continue
        print(f"\n{BLEU}Léa :{RAZ} {reponse}")


if __name__ == "__main__":
    raise SystemExit(main())
