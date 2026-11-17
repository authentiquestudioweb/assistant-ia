"""Boucle de conversation de Léa avec appels d'outils (API compatible OpenAI)."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable

from datetime import date, datetime

from lea.dates import (
    DateParseError,
    format_datetime_fr,
    heures_dans,
    normaliser,
    parse_date,
    parse_datetime,
    parse_time,
)
from lea.prompts import RAPPEL_OUTIL, RAPPEL_RESERVATION, system_prompt
from lea.telephone import (
    extraire_telephone,
    formater_chiffres,
    valider_telephone,
    ressemble_a_un_numero,
    telephone_en_morceaux,
)
from lea.tools import TOOL_SCHEMAS, LeaTools

MESSAGE_ERREUR = "Excusez-moi, j'ai eu un petit souci. Pouvez-vous répéter, s'il vous plaît ?"
MESSAGE_NUMERO_INCOMPRIS = (
    "Ce numéro me semble incomplet. Le plus simple : touchez le bouton "
    "« Taper mon numéro » sous l'appel et tapez-le. Sinon, redites-le en entier, lentement."
)

# Textes normalisés (minuscules, sans accents)
URGENCE_RE = re.compile(r"gonfl|enfl[eu]|fievre|saign|abces")
ANNONCE_RE = re.compile(
    r"je (vais|m'en vais) (maintenant |tout de suite |donc )?"
    r"(chercher|verifier|regarder|consulter|reserver|enregistrer|proceder|noter)"
    r"|je vous rappelle|quelques instants|un instant|patientez"
)
RESERVATION_RE = re.compile(
    r"\b(rendez-vous|rdv)\b[^!?]{0,60}\b(confirme|reserve|enregistre|pris|fixe|cale)\b"
    r"|\b(est|suis|sommes) (maintenant |bien |donc )?(reserve|confirme|enregistre|pris|fixe)\b"
    r"|c'est note|je (vais |viens de )?(vous )?(reserv|enregistr)|j'ai (bien )?(reserve|enregistre)"
)
RECAP_RE = re.compile(r"recapitul|c'est bien (cela|ca)|est-ce (bien )?(cela|ca|correct)|vous confirmez|je confirme")
OUI_RE = re.compile(
    r"^(oui|ouais|exact|exactement|c'est (bien )?(ca|cela|exact|parfait|bon)|parfait|"
    r"d'accord|tout a fait|absolument|ok|okay|confirme|je confirme|c'est correct|correct)\b"
)
REFUS_RESERVATION_DIRECTE = {
    "ok": False,
    "erreur": (
        "Pas encore enregistré. Vérifie que tu as le nom et le téléphone donnés par le "
        "patient, puis récapitule en une phrase : date, heure, motif et nom, terminée par "
        "« C'est bien cela ? ». La réservation se fait automatiquement quand il dit oui."
    ),
}
NON_RE = re.compile(r"^(non|pas (du tout|ca|cela)|ce n'est pas|c'est pas|attendez)\b")
NEGATION_RE = re.compile(r"\b(pas possible|pas dispo|impossible|ne peux pas|peux pas|pas libre)\b")
_MAJ = "A-ZÀ-ÖØ-Ý"
NOM_RE = re.compile(
    rf"(?i:je m'appelle|mon nom (?:est|c'est)|moi,? c'est|je suis|c'est)\s+"
    rf"((?:[Mm]onsieur|[Mm]adame|[Mm]ademoiselle)\s+)?([{_MAJ}][\w'-]+(?:\s+[{_MAJ}][\w'-]+){{0,2}})"
)
NOM_SEUL_RE = re.compile(rf"^\s*([{_MAJ}][\w'-]+(?:\s+[{_MAJ}][\w'-]+){{1,2}})\s*[.!]?\s*$")
_PAS_DES_NOMS = {"lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
                 "oui", "non", "bonjour", "merci", "docteur", "madame", "monsieur", "le", "la"}
PROMPT_EXTRACTION = """Tu lis la transcription d'un appel à un cabinet dentaire.
Extrais les informations du rendez-vous que le patient vient de confirmer.
Réponds UNIQUEMENT avec un objet JSON ayant ces clés :
"nom" (nom et prénom du patient), "tel" (son téléphone), "motif" (court),
"depuis_quand" (depuis combien de temps le patient a ce problème, ex. "trois jours" ;
ce n'est PAS la date du rendez-vous ; null si non dit),
"niveau_douleur" (entier 0-10 ou null),
"creneau" (date et heure du rendez-vous, ex. "lundi 9 novembre 2026 à 9h30").
N'invente rien : mets null si une information n'a pas été donnée."""
_NOMS_OUTILS = {t["function"]["name"] for t in TOOL_SCHEMAS}


def _appels_dans_le_texte(contenu: str) -> list[dict]:
    """Certains petits modèles écrivent l'appel d'outil en JSON dans le texte
    (parfois entre balises <tool_call>) au lieu d'utiliser le champ dédié."""
    if not contenu or "{" not in contenu:
        return []
    # Format parfois produit par Llama : <function=get_free_slots>{"date": "demain"}</function>
    appels_llama = [
        {"id": f"call_{uuid.uuid4().hex[:8]}", "name": nom, "arguments": args}
        for nom, args in re.findall(r"<function=(\w+)>\s*(\{.*?\})\s*(?:</function>|$)", contenu, re.S)
        if nom in _NOMS_OUTILS
    ]
    if appels_llama:
        return appels_llama
    blocs = re.findall(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", contenu, re.S)
    if not blocs:
        s = contenu.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        blocs = [s] if s.startswith("{") else []
    appels = []
    for bloc in blocs:
        try:
            obj = json.loads(bloc)
        except json.JSONDecodeError:
            continue
        nom = obj.get("name") if isinstance(obj, dict) else None
        if nom in _NOMS_OUTILS:
            args = obj.get("arguments", obj.get("parameters", {}))
            appels.append({
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "name": nom,
                "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False),
            })
    return appels


def confirmation_orale(resultat: dict) -> str:
    quand = resultat["confirmation"].removeprefix("Rendez-vous confirmé le ").rstrip(".")
    return (f"C'est parfait, votre rendez-vous est enregistré pour le {quand}. "
            "Merci de votre appel, et à bientôt au cabinet !")


def _nettoyer(texte: str) -> str:
    texte = re.sub(r"<think>.*?(</think>|$)", "", texte or "", flags=re.S)
    texte = re.sub(r"\*\*|__|`", "", texte)
    texte = re.sub(r"\s*\((?:waiting|en attente)[^)]*\)", "", texte, flags=re.I)
    # Certains modèles collent deux questions (« …plaît ?Merci. Quel… ») : une seule à la fois.
    if m := re.search(r"\?(?=[A-ZÀ-Ý])", texte):
        texte = texte[:m.end()]
    texte = texte.replace("\u2011", "-").replace("\u202f", " ").replace("\u00a0", " ")
    texte = re.sub(r"^\s*[-*•]\s+", "", texte, flags=re.M)
    # Tic des petits modèles : « C'est bien cela ? » ajouté après une autre question.
    texte = re.sub(r"(\?[^?]*?)\s*C'est bien (cela|ça) ?\?\s*$", r"\1", texte.strip(), flags=re.I)
    return re.sub(r"\s*\n\s*", " ", texte).strip()


class LeaAgent:
    def __init__(
        self,
        client,
        model: str,
        tools: LeaTools,
        cabinet: str = "cabinet dentaire",
        temperature: float = 0.3,
        max_tool_rounds: int = 6,
        max_tokens: int = 150,
        modeles_secours: tuple[str, ...] = (),
        max_messages: int = 24,
        on_tool_call: Callable[[str, str, dict], None] | None = None,
    ):
        self.client = client
        self.model = model
        self.tools = tools
        self.cabinet = cabinet
        self.temperature = temperature
        self.max_tool_rounds = max_tool_rounds
        self.max_tokens = max_tokens
        self.modeles_secours = tuple(modeles_secours)
        self.max_messages = max_messages
        self.on_tool_call = on_tool_call
        self.reset()

    def reset(self) -> None:
        self.tools.urgence_signalee = False
        self.rdv_confirme = False
        self.creneau_retenu: datetime | None = None
        self.telephone_tape: str | None = None
        self.derniere_date: date | None = None
        self.messages: list[dict] = [{"role": "system", "content": ""}]

    def _rappel_necessaire(self, texte: str, outil_appele: bool) -> str | None:
        t = normaliser(texte)
        if not outil_appele and ANNONCE_RE.search(t):
            return RAPPEL_OUTIL
        if not self.rdv_confirme and not t.startswith("je recapitule") and RESERVATION_RE.search(t):
            return RAPPEL_RESERVATION
        return None

    def greeting(self) -> str:
        texte = f"{self.cabinet}, Léa à l'appareil, bonjour ! Que puis-je faire pour vous ?"
        self.messages.append({"role": "assistant", "content": texte})
        return texte

    def _rafraichir_prompt(self) -> None:
        self.messages[0]["content"] = system_prompt(
            self.cabinet, self.tools.horaires, self.tools.now()
        )

    def _ajouter_heure_demandee(self, arguments: str) -> str:
        """Si le patient vient de dire une heure (« lundi à 10h30 ») et que le
        modèle a oublié de la passer, on l'ajoute : l'outil proposera les
        créneaux autour de cette heure."""
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return arguments
        if not isinstance(args, dict) or args.get("heure"):
            return arguments
        dernier = next((m.get("content") or "" for m in reversed(self.messages)
                        if m["role"] == "user"), "")
        try:
            h = parse_time(dernier)
        except DateParseError:
            h = None
        if h is None:
            return arguments
        args["heure"] = f"{h.hour}h{h.minute:02d}"
        return json.dumps(args, ensure_ascii=False)

    def _executer(self, appels: list[dict], par_le_code: bool = False) -> list[dict]:
        self.messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": a["id"], "type": "function",
                 "function": {"name": a["name"], "arguments": a["arguments"]}}
                for a in appels
            ],
        })
        resultats = []
        for a in appels:
            if a["name"] == "get_free_slots":
                a["arguments"] = self._ajouter_heure_demandee(a["arguments"])
            if a["name"] == "book_appointment" and not par_le_code:
                # Le modèle ne réserve jamais seul (risque de nom/numéro inventés) :
                # la réservation est faite par le code au « oui » du patient.
                resultat = REFUS_RESERVATION_DIRECTE
            else:
                resultat = self.tools.execute(a["name"], a["arguments"])
            resultats.append(resultat)
            if a["name"] == "book_appointment" and resultat.get("ok"):
                self.rdv_confirme = True
            if a["name"] == "get_free_slots" and resultat.get("ok") and resultat.get("date"):
                self.derniere_date = date.fromisoformat(resultat["date"])
            if self.on_tool_call:
                self.on_tool_call(a["name"], a["arguments"], resultat)
            self.messages.append({
                "role": "tool",
                "tool_call_id": a["id"],
                "content": json.dumps(resultat, ensure_ascii=False),
            })
        return resultats

    def respond(self, texte_patient: str) -> str:
        self._rafraichir_prompt()
        precedent = next(
            (m.get("content") or "" for m in reversed(self.messages)
             if m["role"] == "assistant" and m.get("content")), "")
        self.messages.append({"role": "user", "content": texte_patient})

        # Garde-fou : un « oui » juste après le récapitulatif déclenche la
        # réservation par le code (les petits modèles oublient souvent l'outil,
        # et Ollama ignore tool_choice).
        if (not self.rdv_confirme and OUI_RE.search(normaliser(texte_patient))
                and RECAP_RE.search(normaliser(precedent))):
            manque = self._information_manquante()
            if manque:
                self.messages.append({"role": "assistant", "content": manque})
                return manque
            resultat = self._reserver_depuis_conversation(precedent)
            if resultat and resultat.get("ok"):
                # Confirmation dite par le code : instantanée et sans invention
                # (le modèle promettait par exemple des emails inexistants).
                texte = confirmation_orale(resultat)
                self.messages.append({"role": "assistant", "content": texte})
                return texte

        # Garde-fou : les signes d'urgence sont détectés dans le code, sans
        # dépendre du modèle (un 3B peut oublier d'appeler flag_urgence).
        if not self.tools.urgence_signalee and URGENCE_RE.search(normaliser(texte_patient)):
            self._executer([{
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "name": "flag_urgence",
                "arguments": json.dumps({"raison": texte_patient}, ensure_ascii=False),
            }])

        try:
            texte = self._boucle()
        finally:
            # les rappels internes ne restent pas dans l'historique
            self.messages = [m for m in self.messages if not m.get("_rappel")]
        nouveau = self._etape_par_le_code(texte_patient, precedent, texte)
        if nouveau != texte and self.messages and self.messages[-1]["role"] == "assistant":
            self.messages[-1]["content"] = nouveau
        return nouveau

    # ------------------------------------------------------------------
    # Étape décisive tenue par le code : récapitulatif et numéro de téléphone.
    # Un modèle 3B oublie souvent de récapituler, ou dit « c'est pris » sans
    # rien enregistrer ; ici, dès que le créneau, le nom et un numéro valide
    # sont connus, le code pose lui-même la question de confirmation.
    # ------------------------------------------------------------------
    def _messages_patient(self) -> list[str]:
        return [m.get("content") or "" for m in self.messages if m["role"] == "user"]

    def saisir_telephone(self, valeur: str) -> str | None:
        """Numéro tapé au clavier : fiable, il prime sur tout ce qui a été dicté."""
        self.telephone_tape = valider_telephone(valeur)
        return self.telephone_tape

    def telephone_connu(self) -> str | None:
        if self.telephone_tape:
            return self.telephone_tape
        # Du plus récent au plus ancien : un numéro reste connu même si d'autres
        # phrases (« oui, c'est bien ça ») ont suivi sa dictée en plusieurs morceaux.
        messages = self._messages_patient()
        for fin in range(len(messages), 0, -1):
            tel, _ = telephone_en_morceaux(messages[:fin])
            if tel := tel or extraire_telephone(messages[fin - 1]):
                return tel
        return None

    def _information_manquante(self) -> str | None:
        """Question à poser si le patient confirme alors qu'il manque le téléphone ou le nom."""
        if not self.telephone_connu():
            return ("Il me manque votre numéro de téléphone. Touchez le bouton « Taper mon "
                    "numéro » sous l'appel, ou dictez-le chiffre par chiffre.")
        if not self.nom_connu():
            return "Il me manque votre nom. Quels sont votre nom et votre prénom ?"
        return None

    def nom_connu(self) -> str | None:
        for texte in reversed(self._messages_patient()):
            m = NOM_RE.search(texte)
            nom = None
            if m:
                civilite = (m[1] or "").strip().capitalize()
                nom = f"{civilite} {m[2]}".strip()
            elif m2 := NOM_SEUL_RE.match(texte):
                nom = m2[1]
            if nom and normaliser(nom.split()[-1]) not in _PAS_DES_NOMS:
                return nom
        return None

    def _creneau_candidat(self, patient: str, lea: str) -> datetime | None:
        heure = None
        hp = heures_dans(patient)
        if len(hp) == 1 and not NEGATION_RE.search(normaliser(patient)):
            heure = hp[0]
        elif len(hl := heures_dans(lea)) == 1:
            heure = hl[0]
        if heure is None:
            return None
        today = self.tools.now().date()
        jour = None
        for source in (lea, patient):
            try:
                jour = parse_date(source, today)
                break
            except DateParseError:
                continue
        jour = jour or self.derniere_date or (self.creneau_retenu.date() if self.creneau_retenu else None)
        if jour is None:
            return None
        dt = self.tools.horaires.localiser(datetime.combine(jour, heure))
        return dt if dt in self.tools.libres(jour) else None

    def _etape_par_le_code(self, patient: str, precedent: str, texte: str) -> str:
        if self.rdv_confirme:
            return texte
        p = normaliser(patient)
        if NON_RE.search(p) and RECAP_RE.search(normaliser(precedent)):
            self.telephone_tape = None  # le numéro est peut-être ce qu'il corrige
            return texte  # le patient corrige : on laisse Léa l'écouter
        if c := self._creneau_candidat(patient, texte):
            self.creneau_retenu = c
        tel_morceaux, debut = telephone_en_morceaux(self._messages_patient())
        if not self.telephone_tape and not tel_morceaux and not extraire_telephone(patient):
            if debut and len(debut) >= 4:
                return f"J'ai noté {formater_chiffres(debut)}. Quelle est la suite ?"
            if ressemble_a_un_numero(patient):
                return MESSAGE_NUMERO_INCOMPRIS
        if not self.creneau_retenu:
            return texte
        tel, nom = self.telephone_connu(), self.nom_connu()
        if tel and nom:
            return (f"Je récapitule : rendez-vous le {format_datetime_fr(self.creneau_retenu)}, "
                    f"au nom de {nom}, numéro {tel}. C'est bien cela ?")
        return texte

    def _extraire_rdv(self) -> dict:
        """Appel LLM court et ciblé (mode JSON) : bien plus fiable pour un 3B
        que de lui demander de penser à l'outil au milieu de la conversation."""
        transcription = "\n".join(
            f"{'Patient' if m['role'] == 'user' else 'Léa'} : {m['content']}"
            for m in self.messages
            if m["role"] in ("user", "assistant") and m.get("content")
        )
        kwargs = dict(
            messages=[{"role": "system", "content": PROMPT_EXTRACTION},
                      {"role": "user", "content": transcription}],
            temperature=0,
        )
        try:
            resp = self._completer(**kwargs, response_format={"type": "json_object"})
        except Exception:  # noqa: BLE001 - serveur sans mode JSON
            resp = self._completer(**kwargs)
        contenu = resp.choices[0].message.content or ""
        m = re.search(r"\{.*\}", contenu, re.S)
        try:
            data = json.loads(m[0]) if m else {}
        except json.JSONDecodeError:
            data = {}
        return data if isinstance(data, dict) else {}

    def _reserver_depuis_conversation(self, recapitulatif: str) -> dict | None:
        # Le créneau est relu dans la phrase confirmée par le patient (déterministe).
        try:
            creneau = parse_datetime(recapitulatif, self.tools.now().date()).strftime("%Y-%m-%dT%H:%M")
        except DateParseError:
            return None  # pas un vrai récapitulatif (pas de date + heure) : rien à réserver
        data = self._extraire_rdv()
        dit_par_patient = normaliser(" ".join(
            m.get("content") or "" for m in self.messages if m["role"] == "user"))
        nom = self.nom_connu() or data.get("nom")
        if nom and not any(len(mot) > 1 and re.search(rf"\b{re.escape(mot)}\b", dit_par_patient)
                           for mot in normaliser(str(nom)).split()):
            nom = None  # nom inventé par le modèle
        # Le téléphone est repris tel que dicté par le patient.
        tel = self.telephone_connu()
        args = {
            "nom": nom,
            "tel": tel or "non communiqué",
            "motif": data.get("motif") or "consultation",
            "creneau": creneau,
            "depuis_quand": data.get("depuis_quand"),
            "niveau_douleur": data.get("niveau_douleur"),
        }
        return self._executer([{
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "name": "book_appointment",
            "arguments": json.dumps({k: v for k, v in args.items() if v not in (None, "")},
                                    ensure_ascii=False),
        }], par_le_code=True)[0]

    def _completer(self, **kwargs):
        """Appel LLM avec bascule : si le modèle répond « quota atteint » (429) ou
        « appel d'outil mal formé » (400 tool_use_failed), on essaie le suivant."""
        modeles = (self.model, *self.modeles_secours)
        for i, modele in enumerate(modeles):
            try:
                return self.client.chat.completions.create(model=modele, **kwargs)
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "status_code", None)
                # Délai dépassé / réseau (pas de code HTTP), quota, appel d'outil mal formé,
                # modèle retiré ou serveur en panne : le suivant peut répondre.
                recuperable = (
                    code is None or code in (404, 429) or code >= 500
                    or (code == 400 and "tool_use_failed" in str(exc))
                )
                if not recuperable or i == len(modeles) - 1:
                    raise

    def _fenetre_messages(self) -> list[dict]:
        """Système + derniers échanges : garde chaque requête petite (quotas gratuits).
        La fenêtre commence toujours à une phrase du patient, pour ne jamais couper
        une paire appel d'outil / résultat."""
        reste = self.messages[1:]
        if len(reste) > self.max_messages:
            reste = reste[-self.max_messages:]
            while reste and reste[0]["role"] != "user":
                reste = reste[1:]
        return [{k: v for k, v in m.items() if not k.startswith("_")}
                for m in (self.messages[0], *reste)]

    def _appeler_llm(self):
        return self._completer(
            messages=self._fenetre_messages(),
            tools=TOOL_SCHEMAS,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

    def prechauffer(self) -> None:
        """Fait lire au LLM le prompt système et les outils (identiques pour tous
        les appels de la journée) : Ollama les garde en cache, et la première
        réponse à un patient passe de ~25 s à quelques secondes sur CPU."""
        self._rafraichir_prompt()
        self.client.chat.completions.create(
            model=self.model,
            messages=[self.messages[0], {"role": "user", "content": "Bonjour"}],
            tools=TOOL_SCHEMAS,
            temperature=self.temperature,
            max_tokens=1,
        )

    def _boucle(self) -> str:
        outil_appele = False
        rappels = 0
        for _ in range(self.max_tool_rounds):
            resp = self._appeler_llm()
            msg = resp.choices[0].message
            appels = [
                {"id": tc.id or f"call_{uuid.uuid4().hex[:8]}",
                 "name": tc.function.name,
                 "arguments": tc.function.arguments or "{}"}
                for tc in (msg.tool_calls or [])
            ] or _appels_dans_le_texte(msg.content or "")

            if appels:
                outil_appele = True
                self._executer(appels)
                continue

            texte = _nettoyer(msg.content or "")
            rappel = self._rappel_necessaire(texte, outil_appele) if rappels < 2 else None
            if rappel:
                # Le modèle annonce une action sans la faire : on le relance une fois.
                rappels += 1
                self.messages.append({"role": "system", "content": rappel, "_rappel": True})
                continue
            texte = texte or MESSAGE_ERREUR
            self.messages.append({"role": "assistant", "content": texte})
            return texte

        self.messages.append({"role": "assistant", "content": MESSAGE_ERREUR})
        return MESSAGE_ERREUR
