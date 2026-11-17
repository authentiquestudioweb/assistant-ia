"""Outils appelables par le LLM : get_free_slots, book_appointment, flag_urgence.

Les réponses sont volontairement courtes (3 créneaux proposés au plus) et
prêtes à être dites (« 9h30 », « lundi 9 novembre 2026 ») : un modèle 3B
recopie ce qu'il reçoit plutôt que de le reformater.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path

from lea.calendars.base import Calendar, RendezVous
from lea.dates import (
    DateParseError,
    format_date_fr,
    format_datetime_fr,
    format_heure_fr,
    normaliser,
    parse_date,
    parse_datetime,
    parse_time,
)
from lea.scheduling import Horaires, creneaux_libres
from lea.telephone import valider_telephone

NB_PROPOSITIONS = 3
JOURS_MAX = 183  # au-delà de ~6 mois, la date est probablement mal comprise

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_free_slots",
            "description": (
                "Cherche des créneaux libres pour un JOUR donné par le patient. Accepte ses "
                "mots tels quels : 'demain', 'lundi prochain', 'le 12', '3 décembre'. "
                "Renvoie la date complète (date_lisible) et quelques créneaux à proposer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Le jour demandé, ex. 'lundi prochain'. Pas une durée.",
                    },
                    "heure": {
                        "type": "string",
                        "description": "Heure souhaitée si le patient en a donné une, ex. '9h30' ou 'matin'.",
                    },
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": (
                "Enregistre le rendez-vous. À appeler UNIQUEMENT quand tu connais le vrai nom, "
                "le vrai téléphone, le motif et le créneau, ET que le patient a dit oui au "
                "récapitulatif."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nom": {"type": "string", "description": "Nom et prénom donnés par le patient."},
                    "tel": {"type": "string", "description": "Téléphone donné par le patient."},
                    "motif": {"type": "string", "description": "Motif de la consultation."},
                    "creneau": {
                        "type": "string",
                        "description": "Valeur 'creneau' renvoyée par get_free_slots, ex. 2026-11-09T09:30.",
                    },
                    "depuis_quand": {
                        "type": "string",
                        "description": "Depuis quand le problème dure, ex. 'trois jours'.",
                    },
                    "niveau_douleur": {
                        "type": "integer",
                        "description": "Douleur de 0 (aucune) à 10 (insupportable).",
                    },
                },
                "required": ["nom", "tel", "motif", "creneau"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flag_urgence",
            "description": (
                "Signale une urgence à l'équipe et donne les premiers créneaux libres. À appeler "
                "en cas de gonflement (joue, gencive), fièvre ou saignement qui ne s'arrête pas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "raison": {
                        "type": "string",
                        "description": "Symptôme(s) d'urgence décrits par le patient.",
                    }
                },
                "required": [],
            },
        },
    },
]

_NOMS_BIDON = re.compile(
    r"^(non renseigne|inconnu|a preciser|patient|nom|n/?a|none|null|xxx|\?+)$"
)


def normaliser_telephone(tel: str) -> str:
    """Numéro de tout pays, au format lisible. ValueError si invalide."""
    numero = valider_telephone(tel)
    if numero is None:
        raise ValueError(
            f"Numéro de téléphone invalide : « {tel} ». Demander au patient son numéro complet."
        )
    return numero


def _douleur(valeur) -> int | None:
    if valeur is None or valeur == "":
        return None
    m = re.search(r"\d+", str(valeur))
    if not m:
        return None
    return max(0, min(10, int(m[0])))


def _heure_souhaitee(texte: str | None) -> time | None:
    if not texte:
        return None
    t = normaliser(texte)
    try:
        if h := parse_time(t):
            return h
    except DateParseError:
        return None
    if "matin" in t:
        return time(9, 0)
    if "apres-midi" in t or "apres midi" in t or "aprem" in t:
        return time(14, 30)
    if "soir" in t or "fin de journee" in t:
        return time(16, 30)
    return None


def _creneau(s: datetime, avec_date: bool = False) -> dict:
    d = {"heure": format_heure_fr(s), "creneau": s.strftime("%Y-%m-%dT%H:%M")}
    if avec_date:
        d = {"date_lisible": format_date_fr(s.date()), **d}
    return d


def proposer(libres: list[datetime], heure: time | None = None, n: int = NB_PROPOSITIONS) -> list[datetime]:
    """Choisit au plus `n` créneaux : les plus proches de l'heure souhaitée,
    sinon répartis sur la journée (matin, milieu, fin)."""
    if len(libres) <= n:
        return list(libres)
    if heure is not None:
        cible = heure.hour * 60 + heure.minute
        proches = sorted(libres, key=lambda s: abs(s.hour * 60 + s.minute - cible))[:n]
        return sorted(proches)
    idx = sorted({round(i * (len(libres) - 1) / (n - 1)) for i in range(n)})
    return [libres[i] for i in idx]


class LeaTools:
    def __init__(
        self,
        calendar: Calendar,
        horaires: Horaires | None = None,
        now_fn: Callable[[], datetime] | None = None,
        urgences_path: str | Path | None = None,
        on_urgence: Callable[[dict], None] | None = None,
    ):
        self.calendar = calendar
        self.horaires = horaires or Horaires()
        self._now_fn = now_fn
        self.urgences_path = Path(urgences_path) if urgences_path else None
        self.on_urgence = on_urgence
        self.urgence_signalee = False

    def now(self) -> datetime:
        now = self._now_fn() if self._now_fn else datetime.now(self.horaires.tz)
        return self.horaires.localiser(now)

    def libres(self, d: date) -> list[datetime]:
        return creneaux_libres(self.calendar, self.horaires, d, self.now())

    # ------------------------------------------------------------------
    def _prochaine_dispo(self, apres: date, jours: int = 14) -> dict | None:
        d = apres
        for _ in range(jours):
            d += timedelta(days=1)
            if libres := self.libres(d):
                return {
                    "date": d.isoformat(),
                    "date_lisible": format_date_fr(d),
                    "creneaux_proposes": [_creneau(s) for s in proposer(libres)],
                }
        return None

    def premiers_creneaux(self, n: int = NB_PROPOSITIONS, jours: int = 14) -> list[datetime]:
        trouves: list[datetime] = []
        d = self.now().date()
        for _ in range(jours):
            trouves += self.libres(d)
            if len(trouves) >= n:
                break
            d += timedelta(days=1)
        return trouves[:n]

    def get_free_slots(self, date: str, heure: str | None = None) -> dict:
        today = self.now().date()
        try:
            d = parse_date(date, today)
        except DateParseError as exc:
            return {
                "ok": False,
                "erreur": str(exc),
                "aide": "Demande simplement au patient quel jour l'arrange.",
            }
        base = {"ok": True, "date": d.isoformat(), "date_lisible": format_date_fr(d)}
        if d < today:
            return {**base, "ok": False, "erreur": "Cette date est déjà passée."}
        if d > today + timedelta(days=JOURS_MAX):
            # souvent une confusion (« six sur dix » compris comme « le 6 ») :
            return {**base, "ok": False, "erreur": (
                f"Le {format_date_fr(d)} est dans plus de six mois. "
                "Ne cherche pas : demande au patient quel jour il souhaite venir.")}
        if not self.horaires.est_ouvert(d):
            return {
                **base,
                "ouvert": False,
                "creneaux_proposes": [],
                "message": f"Le cabinet est fermé le {format_date_fr(d)}.",
                "prochaine_disponibilite": self._prochaine_dispo(d),
            }
        libres = self.libres(d)
        h = _heure_souhaitee(heure)
        res = {
            **base,
            "ouvert": True,
            "creneaux_proposes": [_creneau(s) for s in proposer(libres, h)],
            "nombre_creneaux_libres": len(libres),
        }
        if h is not None:
            res["heure_souhaitee_libre"] = any(s.time() == h for s in libres)
        if not libres:
            res["message"] = "Plus aucun créneau libre ce jour-là."
            res["prochaine_disponibilite"] = self._prochaine_dispo(d)
        return res

    def book_appointment(
        self,
        nom: str,
        tel: str,
        motif: str,
        creneau: str,
        depuis_quand: str | None = None,
        niveau_douleur: int | str | None = None,
    ) -> dict:
        nom, motif = str(nom or "").strip(), str(motif or "").strip()
        if not nom or _NOMS_BIDON.match(normaliser(nom)):
            return {"ok": False, "erreur": "Nom du patient inconnu : demande-lui son nom et prénom."}
        if not motif:
            return {"ok": False, "erreur": "Motif manquant : demande au patient ce qui l'amène."}
        try:
            telephone = normaliser_telephone(tel)
        except ValueError as exc:
            return {"ok": False, "erreur": str(exc)}
        try:
            debut = self.horaires.localiser(parse_datetime(creneau, self.now().date()))
        except DateParseError as exc:
            return {"ok": False, "erreur": str(exc)}

        libres = self.libres(debut.date())
        autres = [_creneau(s) for s in proposer(libres, debut.time())]
        if not self.horaires.est_creneau_valide(debut):
            return {
                "ok": False,
                "erreur": (
                    f"Le {format_datetime_fr(debut)} n'est pas un créneau du cabinet "
                    f"({self.horaires.ouverture}h-{self.horaires.fermeture}h, "
                    f"créneaux de {self.horaires.duree_creneau} min, jours ouvrés)."
                ),
                "autres_creneaux": autres,
            }
        if debut <= self.now():
            return {"ok": False, "erreur": "Ce créneau est déjà passé."}
        if debut not in libres:
            return {
                "ok": False,
                "erreur": f"Le créneau du {format_datetime_fr(debut)} est déjà pris.",
                "autres_creneaux": autres,
            }

        rdv = RendezVous(
            nom=nom,
            telephone=telephone,
            motif=motif,
            debut=debut,
            fin=debut + self.horaires.duree,
            depuis_quand=(str(depuis_quand).strip() or None) if depuis_quand else None,
            niveau_douleur=_douleur(niveau_douleur),
            urgence=self.urgence_signalee,
        )
        try:
            rdv = self.calendar.create_appointment(rdv)
        except ValueError as exc:
            return {"ok": False, "erreur": str(exc)}
        return {
            "ok": True,
            "id": rdv.id,
            "confirmation": f"Rendez-vous confirmé le {format_datetime_fr(debut)}.",
            "patient": nom,
            "telephone": telephone,
        }

    def flag_urgence(self, raison: str = "") -> dict:
        self.urgence_signalee = True
        evt = {"horodatage": self.now().isoformat(), "raison": str(raison or "").strip()}
        if self.urgences_path:
            self.urgences_path.parent.mkdir(parents=True, exist_ok=True)
            with self.urgences_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(evt, ensure_ascii=False) + "\n")
        if self.on_urgence:
            self.on_urgence(evt)
        return {
            "ok": True,
            "message": "Urgence transmise à l'équipe, le patient sera traité en priorité.",
            "premiers_creneaux_libres": [_creneau(s, avec_date=True) for s in self.premiers_creneaux()],
            "consigne": (
                "Dis au patient que l'équipe est prévenue et que le cabinet va le prendre en "
                "charge en priorité, puis propose le premier de ces créneaux. Ne l'envoie ni à "
                "l'hôpital ni chez un médecin. Seulement s'il a du mal à respirer ou à avaler : "
                "appeler le 15."
            ),
        }

    # ------------------------------------------------------------------
    def execute(self, name: str, arguments: str | dict | None) -> dict:
        fonctions = {
            "get_free_slots": self.get_free_slots,
            "book_appointment": self.book_appointment,
            "flag_urgence": self.flag_urgence,
        }
        if name not in fonctions:
            return {"ok": False, "erreur": f"Outil inconnu : {name}"}
        try:
            args = json.loads(arguments) if isinstance(arguments, str) and arguments.strip() else (arguments or {})
        except json.JSONDecodeError:
            return {"ok": False, "erreur": "Arguments JSON invalides."}
        if not isinstance(args, dict):
            return {"ok": False, "erreur": "Arguments invalides."}
        # les petits modèles ajoutent parfois des champs inventés : on les ignore
        accepte = inspect.signature(fonctions[name]).parameters
        args = {k: v for k, v in args.items() if k in accepte and v not in (None, "")}
        try:
            return fonctions[name](**args)
        except TypeError as exc:
            manquants = re.findall(r"'(\w+)'", str(exc))
            return {
                "ok": False,
                "erreur": f"Informations manquantes pour {name} : {', '.join(manquants) or exc}. "
                          "Demande-les au patient avant de rappeler l'outil.",
            }
