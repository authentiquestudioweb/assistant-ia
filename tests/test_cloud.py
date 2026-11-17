"""Mode cloud : Groq, bascule de modèle, Google Calendar, code d'accès, téléphone en morceaux."""

import json
from datetime import datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest

from lea.agent import LeaAgent
from lea.calendars.base import RendezVous
from lea.calendars.google import GoogleCalendar
from lea.config import GROQ_BASE_URL, load_settings
from lea.telephone import est_une_dictee, ressemble_a_un_numero, telephone_en_morceaux

PARIS = ZoneInfo("Europe/Paris")


def reponse(content=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=None))])


class Erreur(Exception):
    def __init__(self, code, msg=""):
        super().__init__(msg)
        self.status_code = code


class Client:
    def __init__(self, sorties):
        self.sorties, self.modeles, self.appels = list(sorties), [], []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.modeles.append(kw["model"])
        self.appels.append(kw["messages"])
        s = self.sorties.pop(0)
        if isinstance(s, Exception):
            raise s
        return s


# --- configuration ---------------------------------------------------------

def _env(monkeypatch, **valeurs):
    for nom in ("GROQ_API_KEY", "MOTEUR", "LLM_BASE_URL", "GROQ_LLM_MODEL", "GROQ_LLM_FALLBACK"):
        monkeypatch.setenv(nom, "") if nom == "GROQ_API_KEY" else monkeypatch.delenv(nom, raising=False)
    for k, v in valeurs.items():
        monkeypatch.setenv(k, v)


def test_cle_groq_active_le_moteur_cloud(monkeypatch):
    _env(monkeypatch, GROQ_API_KEY="gsk_test")
    s = load_settings()
    assert (s.moteur, s.llm_base_url, s.llm_api_key) == ("groq", GROQ_BASE_URL, "gsk_test")
    assert s.llm_model == "openai/gpt-oss-120b"
    assert s.llm_fallback_models == ("openai/gpt-oss-20b", "qwen/qwen3.8-27b")


def test_moteur_local_force_malgre_la_cle(monkeypatch):
    _env(monkeypatch, GROQ_API_KEY="gsk_test", MOTEUR="local", LLM_BASE_URL="http://localhost:11434/v1")
    s = load_settings()
    assert s.moteur == "local" and "11434" in s.llm_base_url and s.llm_fallback_models == ()


# --- bascule de modèle et fenêtre d'historique ------------------------------

def test_quota_atteint_bascule_sur_le_modele_de_secours(tools):
    client = Client([Erreur(429), reponse("Bonjour !")])
    agent = LeaAgent(client, "gros", tools, modeles_secours=("petit",))
    assert agent.respond("allo") == "Bonjour !"
    assert client.modeles == ["gros", "petit"]


def test_appel_outil_mal_forme_bascule_aussi(tools):
    client = Client([Erreur(400, "tool_use_failed"), reponse("Bonjour !")])
    assert LeaAgent(client, "gros", tools, modeles_secours=("petit",)).respond("allo") == "Bonjour !"


def test_delai_depasse_bascule_aussi(tools):
    client = Client([TimeoutError("trop long"), reponse("Bonjour !")])
    assert LeaAgent(client, "gros", tools, modeles_secours=("petit",)).respond("allo") == "Bonjour !"


def test_numero_en_morceaux_reste_connu_apres_le_oui(tools, calendar):
    """Bug trouvé avec le vrai Groq : le « oui » faisait oublier le numéro dicté en deux fois."""
    client = Client([reponse("Très bien."), reponse("Très bien."),
                     reponse('{"nom": "Emmanuel Martin", "motif": "molaire"}')])
    agent = LeaAgent(client, "m", tools)
    agent.creneau_retenu = tools.horaires.localiser(datetime(2026, 11, 12, 10, 0))
    agent.messages.append({"role": "user", "content": "Je m'appelle Emmanuel Martin."})
    agent.respond("Mon numéro est le 06 12")
    agent.respond("34 56 78")
    r = agent.respond("Oui, c'est bien ça.")
    assert r.startswith("C'est parfait, votre rendez-vous est enregistré")
    assert calendar.list_appointments()[0].telephone == "06 12 34 56 78"


def test_autre_erreur_ou_plus_de_modele_remonte(tools):
    with pytest.raises(Erreur):
        LeaAgent(Client([Erreur(401)]), "gros", tools, modeles_secours=("petit",)).respond("allo")
    with pytest.raises(Erreur):
        LeaAgent(Client([Erreur(429), Erreur(429)]), "gros", tools,
                 modeles_secours=("petit",)).respond("allo")


def test_fenetre_ne_coupe_pas_une_paire_outil(tools):
    client = Client([reponse("ok")])
    agent = LeaAgent(client, "m", tools, max_messages=6)
    for i in range(10):
        agent.messages += [
            {"role": "user", "content": f"q{i}"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"c{i}", "type": "function",
                 "function": {"name": "get_free_slots", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": "{}"},
            {"role": "assistant", "content": "r"},
        ]
    agent.respond("derniere")
    envoyes = client.appels[0]
    assert envoyes[0]["role"] == "system" and envoyes[1]["role"] == "user"
    assert len(envoyes) <= 7
    assert envoyes[-1]["content"] == "derniere"


def test_une_seule_question_a_la_fois():
    from lea.agent import _nettoyer
    assert _nettoyer("Votre numéro, s'il vous plaît ?Merci. Quel est votre nom ?") == "Votre numéro, s'il vous plaît ?"
    assert _nettoyer("Quel jour ? Lundi ou mardi ?") == "Quel jour ? Lundi ou mardi ?"


def test_appel_format_llama_dans_le_texte(tools):
    client = Client([reponse('<function=get_free_slots>{"date": "demain"}</function>'), reponse("Voilà.")])
    assert LeaAgent(client, "m", tools).respond("demain ?") == "Voilà."
    assert json.loads(client.appels[1][-1]["content"])["date"] == "2026-11-12"


# --- téléphone en plusieurs morceaux ---------------------------------------

@pytest.mark.parametrize("texte", [
    "le 12 novembre à 10h30", "jeudi 12 novembre 2026", "7 sur 10 depuis 15 jours", "à 10 heures 30",
])
def test_dates_et_heures_ne_sont_pas_un_numero(texte):
    assert not ressemble_a_un_numero(texte)


def test_est_une_dictee():
    assert est_une_dictee("Mon numéro est le 06 12")
    assert est_une_dictee("zéro six")
    assert not est_une_dictee("une dent depuis deux jours")


def test_numero_dicte_en_plusieurs_phrases():
    assert telephone_en_morceaux(["J'ai mal", "06 12", "trente-quatre", "56 78"]) == ("06 12 34 56 78", None)
    assert telephone_en_morceaux(["Mon numéro est le 06 12", "34"]) == (None, "061234")
    assert telephone_en_morceaux(["J'ai mal à une dent"]) == (None, None)
    assert telephone_en_morceaux(["66 47", "99 66"]) == ("66 47 99 66", None)
    assert telephone_en_morceaux(["66 47", "99 66", "66"]) == ("66 47 99 66 66", None)
    assert telephone_en_morceaux(["06 12", "07 11 22 33 44"]) == ("07 11 22 33 44", None)


def test_agent_relit_le_debut_puis_recapitule(tools, calendar):
    client = Client([reponse("Très bien."), reponse("Très bien."), reponse("Très bien.")])
    agent = LeaAgent(client, "m", tools)
    agent.creneau_retenu = tools.horaires.localiser(datetime(2026, 11, 12, 10, 0))
    agent.messages.append({"role": "user", "content": "Je m'appelle Marie Dupont"})
    assert agent.respond("Mon numéro est le 06 12") == "J'ai noté 06 12. Quelle est la suite ?"
    assert agent.respond("34 56") == "J'ai noté 06 12 34 56. Quelle est la suite ?"
    assert agent.respond("78").startswith("Je récapitule : rendez-vous le jeudi 12 novembre 2026 à 10h")
    assert agent.telephone_connu() == "06 12 34 56 78"


def test_appel_reel_numero_hors_france_tape_au_clavier(tools, calendar):
    """Appel réel du 8 novembre : le numéro tapé était refusé car il ne commençait pas par 0."""
    client = Client([reponse("Très bien."), reponse('{"nom": "Emmanuel Bagri", "motif": "douleur"}')])
    agent = LeaAgent(client, "m", tools)
    agent.creneau_retenu = tools.horaires.localiser(datetime(2026, 11, 12, 16, 30))
    agent.messages.append({"role": "user", "content": "Moi, c'est Monsieur Emmanuel Bagri."})
    numero = agent.saisir_telephone("55 66 47 96 66")
    assert numero == "55 66 47 96 66"
    r = agent.respond(f"Mon numéro de téléphone est le {numero}")
    assert r.startswith("Je récapitule") and "55 66 47 96 66" in r
    assert agent.respond("Oui, il sait bien cela.").startswith("C'est parfait")
    assert calendar.list_appointments()[0].telephone == "55 66 47 96 66"


def test_numero_tape_prime_sur_la_dictee_et_non_l_efface(tools):
    agent = LeaAgent(Client([reponse("D'accord.")]), "m", tools)
    agent.messages.append({"role": "user", "content": "66 47 99 66"})
    agent.saisir_telephone("70 11 22 33")
    assert agent.telephone_connu() == "70 11 22 33"
    agent.messages.append({"role": "assistant", "content": "Je récapitule. C'est bien cela ?"})
    agent.respond("Non, ce n'est pas le bon")
    assert agent.telephone_tape is None


def test_oui_sans_numero_redemande_sans_appeler_le_llm(tools):
    client = Client([])
    agent = LeaAgent(client, "m", tools)
    agent.messages.append({"role": "assistant", "content": "Lundi 16 novembre à 8h. C'est bien cela ?"})
    assert "Taper mon numéro" in agent.respond("oui")
    assert client.modeles == []


# --- Google Calendar (faux service : aucune connexion) ----------------------

class FauxEvents:
    def __init__(self):
        self.items, self.params = [], []

    def list(self, **kw):
        self.params.append(kw)
        return NS(execute=lambda: {"items": self.items})

    def insert(self, calendarId, body):
        def execute():
            ev = {**body, "id": f"ev{len(self.items)}"}
            self.items.append(ev)
            return ev
        return NS(execute=execute)


def test_google_cree_puis_relit_un_rendez_vous():
    events = FauxEvents()
    cal = GoogleCalendar(calendar_id="moi@gmail.com", service=NS(events=lambda: events))
    debut = datetime(2026, 11, 12, 10, 0, tzinfo=PARIS)
    rdv = cal.create_appointment(RendezVous(
        nom="Marie Dupont", telephone="06 12 34 56 78", motif="molaire", debut=debut,
        fin=debut.replace(minute=30), depuis_quand="une semaine", niveau_douleur=7, urgence=True,
    ))
    assert rdv.id == "ev0"
    assert "Motif : molaire" in events.items[0]["description"]  # motif dans la description

    assert cal.busy_intervals(debut, debut.replace(hour=17)) == [(debut, debut.replace(minute=30))]

    relu = cal.list_appointments(debut)[0]
    assert (relu.nom, relu.telephone, relu.motif) == ("Marie Dupont", "06 12 34 56 78", "molaire")
    assert (relu.niveau_douleur, relu.depuis_quand, relu.urgence) == (7, "une semaine", True)


def test_google_ignore_annules_et_transparents():
    events = FauxEvents()
    d = {"dateTime": "2026-11-12T10:00:00+01:00"}
    events.items = [{"status": "cancelled", "start": d, "end": d},
                    {"transparency": "transparent", "start": d, "end": d}]
    cal = GoogleCalendar(calendar_id="x", service=NS(events=lambda: events))
    assert cal.busy_intervals(datetime(2026, 11, 12, tzinfo=PARIS), datetime(2026, 11, 13, tzinfo=PARIS)) == []


def test_compte_de_service_refuse_primary():
    pytest.importorskip("googleapiclient")
    with pytest.raises(RuntimeError, match="primary"):
        GoogleCalendar(calendar_id="primary", service_account_json="{}")
