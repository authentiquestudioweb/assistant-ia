"""Boucle de l'agent avec un faux client LLM (pas besoin d'Ollama)."""

import json

import pytest
from types import SimpleNamespace as NS

from lea.agent import LeaAgent


def tool_call(name, args, id_="c1"):
    return NS(id=id_, type="function", function=NS(name=name, arguments=json.dumps(args)))


def reponse(content=None, tool_calls=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls))])


class FauxClient:
    def __init__(self, reponses):
        self.reponses = list(reponses)
        self.appels = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.appels.append(json.loads(json.dumps(kw["messages"], default=str)))
        return self.reponses.pop(0)


def test_le_modele_ne_peut_pas_reserver_seul(tools, calendar):
    client = FauxClient([
        reponse(tool_calls=[tool_call("book_appointment", {
            "nom": "Dupont", "tel": "0788776655",
            "motif": "détartrage", "creneau": "2026-11-12T11:00",
        })]),
        reponse("Quel est votre nom, s'il vous plaît ?"),
    ])
    agent = LeaAgent(client, "faux", tools, cabinet="Cabinet test")
    agent.greeting()
    assert agent.respond("Jeudi 11h") == "Quel est votre nom, s'il vous plaît ?"
    assert calendar.list_appointments() == []
    dernier = client.appels[1][-1]
    assert dernier["role"] == "tool" and json.loads(dernier["content"])["ok"] is False
    # la date du jour est dans le prompt système
    assert "mercredi 11 novembre 2026" in client.appels[0][0]["content"]


def test_appel_outil_ecrit_dans_le_texte(tools):
    """Repli pour les petits modèles qui écrivent l'appel en JSON."""
    client = FauxClient([
        reponse('<tool_call>{"name": "get_free_slots", "arguments": {"date": "demain"}}</tool_call>'),
        reponse("Demain, j'ai 8h, 8h30 ou 9h."),
    ])
    agent = LeaAgent(client, "faux", tools)
    assert agent.respond("Vous avez de la place demain ?") == "Demain, j'ai 8h, 8h30 ou 9h."
    resultat = json.loads(client.appels[1][-1]["content"])
    assert resultat["date"] == "2026-11-12"


def test_urgence(tools):
    client = FauxClient([
        reponse(tool_calls=[tool_call("flag_urgence", {"raison": "joue gonflée"})]),
        reponse("Je préviens l'équipe tout de suite."),
    ])
    agent = LeaAgent(client, "faux", tools)
    agent.respond("J'ai la joue toute gonflée")
    assert tools.urgence_signalee is True
    agent.reset()
    assert tools.urgence_signalee is False


def test_markdown_nettoye_et_boucle_bornee(tools):
    agent = LeaAgent(FauxClient([reponse("**Bonjour** !")]), "faux", tools)
    assert agent.respond("allo") == "Bonjour !"

    en_boucle = FauxClient([reponse(tool_calls=[tool_call("get_free_slots", {"date": "demain"})])] * 10)
    agent = LeaAgent(en_boucle, "faux", tools, max_tool_rounds=3)
    assert "souci" in agent.respond("demain ?")
    assert len(en_boucle.appels) == 3


def test_urgence_detectee_par_le_code_meme_si_le_modele_oublie(tools):
    agent = LeaAgent(FauxClient([reponse("Je suis désolée, je vous écoute.")]), "faux", tools)
    agent.respond("Ma gencive saigne depuis hier soir")
    assert tools.urgence_signalee is True


def test_relance_si_le_modele_annonce_sans_agir(tools):
    client = FauxClient([
        reponse("Je vais chercher les créneaux pour lundi prochain, je vous rappelle."),
        reponse(tool_calls=[tool_call("get_free_slots", {"date": "lundi prochain"})]),
        reponse("Lundi 16 novembre, j'ai 8h, 12h ou 16h30."),
    ])
    agent = LeaAgent(client, "faux", tools)
    assert agent.respond("Lundi prochain").startswith("Lundi 16 novembre")
    assert any(m["role"] == "system" and "Rappel" in m["content"] for m in client.appels[1])
    # le rappel ne reste pas dans l'historique
    assert not any(m.get("_rappel") for m in agent.messages)


def test_pas_de_faux_reserve(tools):
    client = FauxClient([
        reponse("Votre rendez-vous est maintenant réservé pour lundi."),
        reponse("Il me manque votre numéro de téléphone."),
    ])
    agent = LeaAgent(client, "faux", tools)
    assert agent.respond("Marie Dupont") == "Il me manque votre numéro de téléphone."


def test_oui_apres_recapitulatif_reserve_par_le_code(tools, calendar):
    client = FauxClient([
        # 1) extraction JSON
        reponse(json.dumps({"nom": "Marie Dupont", "tel": None, "motif": "contrôle",
                            "depuis_quand": None, "niveau_douleur": 2, "creneau": "n'importe"})),
    ])
    agent = LeaAgent(client, "faux", tools)
    agent.messages += [
        {"role": "user", "content": "Marie Dupont"},
        {"role": "user", "content": "Mon numéro c'est le 06 12 34 56 78"},
        {"role": "assistant", "content":
            "Je récapitule : le jeudi 12 novembre 2026 à 11h, pour un contrôle, "
            "au nom de Marie Dupont. C'est bien cela ?"},
    ]
    assert agent.respond("Oui c'est bien ça") == (
        "C'est parfait, votre rendez-vous est enregistré pour le jeudi 12 novembre 2026 "
        "à 11h. Merci de votre appel, et à bientôt au cabinet !")
    rdv = calendar.list_appointments()[0]
    assert rdv.nom == "Marie Dupont" and rdv.telephone == "06 12 34 56 78"
    assert rdv.debut.hour == 11 and rdv.debut.day == 12  # relu dans le récapitulatif
    assert agent.rdv_confirme
    # la confirmation ne coûte qu'un appel LLM (l'extraction), pas deux
    assert len(client.appels) == 1


def test_oui_sans_nom_ne_reserve_pas(tools, calendar):
    client = FauxClient([
        reponse('{"nom": null, "motif": "urgence"}'),
        reponse("Pouvez-vous me donner votre nom ?"),
    ])
    agent = LeaAgent(client, "faux", tools)
    agent.messages.append({"role": "assistant", "content": "Je vous propose lundi 16 novembre à 8h. C'est bien cela ?"})
    agent.respond("oui")
    assert calendar.list_appointments() == []
    assert not agent.rdv_confirme


def test_nom_et_tel_inventes_rejetes(tools, calendar):
    client = FauxClient([
        reponse('{"nom": "Jean Martin", "motif": "contrôle"}'),
        reponse("Quel est votre nom ?"),
    ])
    agent = LeaAgent(client, "faux", tools)
    agent.messages.append({"role": "assistant", "content":
        "Je récapitule : le jeudi 12 novembre 2026 à 11h, pour un contrôle. C'est bien cela ?"})
    agent.respond("oui")
    assert calendar.list_appointments() == []


def test_faux_confirme_relance_meme_avec_question(tools):
    client = FauxClient([
        reponse("Votre rendez-vous est confirmé le lundi à 9h30. C'est bien cela ?"),
        reponse("Quel est votre numéro de téléphone ?"),
    ])
    agent = LeaAgent(client, "faux", tools)
    assert agent.respond("Marie Dupont") == "Quel est votre numéro de téléphone ?"


def test_tic_c_est_bien_cela_retire():
    from lea.agent import _nettoyer
    assert _nettoyer("Quel est votre nom ? C'est bien cela ?") == "Quel est votre nom ?"
    recap = "Je récapitule : lundi 9 novembre à 9h30. C'est bien cela ?"
    assert _nettoyer(recap) == recap


def test_heure_dite_par_le_patient_transmise_a_l_outil(tools):
    client = FauxClient([
        reponse(tool_calls=[tool_call("get_free_slots", {"date": "lundi"})]),
        reponse("Lundi, j'ai 10h, 10h30 ou 11h."),
    ])
    agent = LeaAgent(client, "faux", tools)
    agent.respond("Est-ce que c'est disponible le lundi à 10h30 ?")
    resultat = json.loads(client.appels[1][-1]["content"])
    assert [c["heure"] for c in resultat["creneaux_proposes"]] == ["10h", "10h30", "11h"]
    assert resultat["heure_souhaitee_libre"] is True


# --- Récapitulatif tenu par le code (rejoue l'appel réel du 7 novembre) ------

def test_appel_reel_recapitulatif_puis_reservation(tools, calendar):
    """Le modèle disait « votre rendez-vous est pris » sans récapituler : le code
    redemande le numéro mal transcrit, puis récapitule et réserve au « oui »."""
    client = FauxClient([
        reponse(tool_calls=[tool_call("get_free_slots", {"date": "mardi"})]),
        reponse("Voici quelques heures disponibles pour le mardi 17 novembre : 8h, 12h et 16h30."),
        reponse("Je vois, le créneau de 10h est disponible pour le mardi 17 novembre. "
                "Pourriez-vous me donner votre nom et votre numéro de téléphone ?"),
        # numéro mal transcrit (8 chiffres) : le code redemande, quoi que dise le modèle
        reponse("Merci Monsieur Emmanuel."),
        reponse("Très bien, merci."),
        # au « oui » : extraction JSON
        reponse('{"nom": "Monsieur Emmanuel", "motif": "mal à une molaire", '
                '"depuis_quand": "une semaine", "niveau_douleur": 7}'),
    ])
    agent = LeaAgent(client, "faux", tools)
    agent.greeting()
    agent.respond("Quelles sont les heures disponibles pour le mardi ?")
    agent.respond("Dans ce cas, je vais prendre 10 heures.")
    assert agent.creneau_retenu.hour == 10 and agent.creneau_retenu.day == 17

    r = agent.respond("Moi, c'est Monsieur Emmanuel, et mon numéro de téléphone, c'est le 7647-95.")
    assert "Taper mon numéro" in r  # trop court : numéro redemandé, clavier proposé

    r = agent.respond("C'est le 07 64 79 56 61.")
    assert r == ("Je récapitule : rendez-vous le mardi 17 novembre 2026 à 10h, "
                 "au nom de Monsieur Emmanuel, numéro 07 64 79 56 61. C'est bien cela ?")

    r = agent.respond("Oui.")
    assert r.startswith("C'est parfait, votre rendez-vous est enregistré pour le mardi 17 novembre 2026 à 10h")
    rdv = calendar.list_appointments()[0]
    assert (rdv.nom, rdv.telephone, rdv.niveau_douleur) == ("Monsieur Emmanuel", "07 64 79 56 61", 7)


def test_non_au_recapitulatif_laisse_le_patient_corriger(tools):
    client = FauxClient([reponse("D'accord, quelle heure préférez-vous ?")])
    agent = LeaAgent(client, "faux", tools)
    agent.creneau_retenu = tools.horaires.localiser(__import__("datetime").datetime(2026, 11, 12, 9, 0))
    agent.messages += [
        {"role": "user", "content": "Je m'appelle Marie Dupont, 06 12 34 56 78"},
        {"role": "assistant", "content": "Je récapitule : ... C'est bien cela ?"},
    ]
    assert agent.respond("Non, je préfère l'après-midi") == "D'accord, quelle heure préférez-vous ?"


@pytest.mark.parametrize("phrase, nom", [
    ("Je m'appelle Marie Dupont", "Marie Dupont"),
    ("Moi, c'est Monsieur Emmanuel, et mon numéro", "Monsieur Emmanuel"),
    ("Marie Dupont.", "Marie Dupont"),
    ("Lundi prochain.", None),
    ("je suis disponible lundi", None),
])
def test_nom_connu(tools, phrase, nom):
    agent = LeaAgent(FauxClient([]), "faux", tools)
    agent.messages.append({"role": "user", "content": phrase})
    assert agent.nom_connu() == nom


def test_rendez_vous_pris_declenche_la_relance(tools):
    from lea.agent import RESERVATION_RE
    from lea.dates import normaliser
    assert RESERVATION_RE.search(normaliser("Merci, votre rendez-vous est pris pour mardi."))
