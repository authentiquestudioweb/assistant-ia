import json
from datetime import date, datetime

import pytest

from conftest import PARIS, make_tools
from lea.calendars.mock import MockCalendar
from lea.tools import normaliser_telephone


def reserver(tools, **kw):
    args = dict(nom="Marie Dupont", tel="06 12 34 56 78", motif="douleur molaire",
                creneau="2026-11-12T09:30")
    args.update(kw)
    return tools.book_appointment(**args)


# --- créneaux libres ------------------------------------------------------

def test_creneaux_journee_complete(tools):
    res = tools.get_free_slots("demain")  # jeudi 12 novembre
    assert res["ok"] and res["ouvert"]
    assert res["date"] == "2026-11-12"
    assert res["date_lisible"] == "jeudi 12 novembre 2026"
    assert res["nombre_creneaux_libres"] == 18  # 8h-17h en créneaux de 30 min
    # 3 propositions réparties sur la journée, prêtes à être dites
    assert [c["heure"] for c in res["creneaux_proposes"]] == ["8h", "12h", "16h30"]
    assert res["creneaux_proposes"][0]["creneau"] == "2026-11-12T08:00"


def test_creneaux_aujourdhui_exclut_le_passe(tools):
    res = tools.get_free_slots("aujourd'hui")
    assert res["creneaux_proposes"][0]["heure"] == "10h30"  # il est 10h00
    assert res["nombre_creneaux_libres"] == 13


def test_lundi_prochain(tools):
    res = tools.get_free_slots("lundi prochain")
    assert res["date"] == "2026-11-16"
    assert res["date_lisible"] == "lundi 16 novembre 2026"


def test_weekend_ferme_avec_prochaine_dispo(tools):
    res = tools.get_free_slots("samedi")
    assert res["ouvert"] is False and res["creneaux_proposes"] == []
    assert res["prochaine_disponibilite"]["date"] == "2026-11-16"


def test_date_passee_et_incomprise(tools):
    assert tools.get_free_slots("2026-11-02")["ok"] is False
    assert tools.get_free_slots("un de ces jours")["ok"] is False


# --- prise de rendez-vous -------------------------------------------------

def test_reservation_ok_et_persistance(tools, calendar):
    res = reserver(tools, depuis_quand="trois jours", niveau_douleur="7/10")
    assert res["ok"], res
    assert res["confirmation"] == "Rendez-vous confirmé le jeudi 12 novembre 2026 à 9h30."

    rdvs = calendar.list_appointments()
    assert len(rdvs) == 1
    r = rdvs[0]
    assert r.nom == "Marie Dupont" and r.telephone == "06 12 34 56 78"
    assert r.motif == "douleur molaire" and r.depuis_quand == "trois jours"
    assert r.niveau_douleur == 7
    assert r.debut == datetime(2026, 11, 12, 9, 30, tzinfo=PARIS)
    assert (r.fin - r.debut).seconds == 30 * 60

    # relu depuis le disque par une nouvelle instance
    assert MockCalendar(calendar.path).list_appointments()[0].id == r.id
    # le créneau disparaît des disponibilités
    assert datetime(2026, 11, 12, 9, 30, tzinfo=PARIS) not in tools.libres(date(2026, 11, 12))


def test_creneau_deja_pris(tools):
    assert reserver(tools)["ok"]
    res = reserver(tools, nom="Paul Martin", tel="0700000000")
    assert res["ok"] is False
    assert "déjà pris" in res["erreur"]
    heures = [c["heure"] for c in res["autres_creneaux"]]
    assert heures == ["8h30", "9h", "10h"]  # les plus proches de 9h30, encore libres


@pytest.mark.parametrize(
    "creneau",
    [
        "2026-11-12T07:30",  # avant l'ouverture
        "2026-11-12T17:00",  # fin = 17h30, après la fermeture
        "2026-11-12T09:15",  # pas aligné sur 30 min
        "2026-11-14T10:00",  # samedi
    ],
)
def test_creneau_hors_horaires(tools, creneau):
    res = reserver(tools, creneau=creneau)
    assert res["ok"] is False and "n'est pas un créneau" in res["erreur"]


def test_dernier_creneau_16h30_accepte(tools):
    assert reserver(tools, creneau="2026-11-12T16:30")["ok"]


def test_creneau_passe(tools):
    res = reserver(tools, creneau="2026-11-11T09:00")
    assert res["ok"] is False


def test_creneau_en_langage_naturel(tools, calendar):
    res = reserver(tools, creneau="lundi prochain à 14h")
    assert res["ok"], res
    assert calendar.list_appointments()[0].debut == datetime(2026, 11, 16, 14, 0, tzinfo=PARIS)


def test_telephone_invalide(tools):
    res = reserver(tools, tel="1234")
    assert res["ok"] is False and "téléphone" in res["erreur"]


@pytest.mark.parametrize(
    "brut, attendu",
    [
        ("0612345678", "06 12 34 56 78"),
        ("06.12.34.56.78", "06 12 34 56 78"),
        ("+33 6 12 34 56 78", "06 12 34 56 78"),
        ("0033612345678", "06 12 34 56 78"),
    ],
)
def test_normaliser_telephone(brut, attendu):
    assert normaliser_telephone(brut) == attendu


def test_champs_obligatoires(tools):
    assert reserver(tools, nom=" ")["ok"] is False
    assert reserver(tools, motif="")["ok"] is False


# --- urgences -------------------------------------------------------------

def test_flag_urgence_journalise_et_marque_le_rdv(tools, calendar, tmp_path):
    res = tools.flag_urgence(raison="joue gonflée et fièvre")
    assert res["ok"]
    lignes = (tmp_path / "urgences.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lignes[0])["raison"] == "joue gonflée et fièvre"

    assert reserver(tools)["ok"]
    rdv = calendar.list_appointments()[0]
    assert rdv.urgence is True
    assert "URGENCE" in rdv.description()


# --- dispatch JSON utilisé par l'agent ------------------------------------

def test_execute_depuis_json(tools):
    res = tools.execute("get_free_slots", '{"date": "demain"}')
    assert res["date"] == "2026-11-12"
    assert tools.execute("inconnu", "{}")["ok"] is False
    assert tools.execute("get_free_slots", "pas du json")["ok"] is False
    assert tools.execute("get_free_slots", '{"jour": "demain"}')["ok"] is False


def test_vendredi_apres_16h30_propose_lundi(calendar, horaires):
    t = make_tools(calendar, horaires, datetime(2026, 11, 13, 16, 45, tzinfo=PARIS))
    res = t.get_free_slots("aujourd'hui")
    assert res["creneaux_proposes"] == []
    assert res["prochaine_disponibilite"]["date"] == date(2026, 11, 16).isoformat()


def test_heure_souhaitee(tools):
    res = tools.get_free_slots("demain", heure="9h30")
    assert res["heure_souhaitee_libre"] is True
    assert [c["heure"] for c in res["creneaux_proposes"]] == ["9h", "9h30", "10h"]
    res = tools.get_free_slots("demain", heure="l'après-midi")
    assert all(c["heure"].startswith(("14", "15")) for c in res["creneaux_proposes"])


def test_urgence_donne_les_premiers_creneaux(tools):
    res = tools.flag_urgence(raison="saignement")
    premiers = res["premiers_creneaux_libres"]
    assert premiers[0] == {
        "date_lisible": "mercredi 11 novembre 2026", "heure": "10h30", "creneau": "2026-11-11T10:30",
    }
    assert len(premiers) == 3


@pytest.mark.parametrize("nom", ["Non renseigné", "inconnu", "?"])
def test_nom_bidon_refuse(tools, nom):
    res = reserver(tools, nom=nom)
    assert res["ok"] is False and "nom" in res["erreur"]


def test_creneau_iso_abime_par_le_modele(tools, calendar):
    assert reserver(tools, creneau="2026-11-12TH09:30")["ok"]
    assert calendar.list_appointments()[0].debut.hour == 9


def test_arguments_inventes_ignores(tools):
    res = tools.execute("book_appointment", json.dumps({
        "nom": "Marie Dupont", "tel": "0612345678", "motif": "contrôle",
        "creneau": "2026-11-12T11:00", "email": "x", "niveau_douleur": None,
    }))
    assert res["ok"], res
    manque = tools.execute("book_appointment", '{"nom": "Marie Dupont"}')
    assert manque["ok"] is False and "tel" in manque["erreur"]


def test_chemins_relatifs_au_projet(monkeypatch, tmp_path):
    from lea.config import PROJECT_ROOT, load_settings
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MOCK_CALENDAR_PATH", raising=False)
    s = load_settings()
    assert s.mock_calendar_path == PROJECT_ROOT / "data" / "calendar.json"
    assert (PROJECT_ROOT / "pyproject.toml").exists()


def test_date_trop_lointaine_refusee(tools):
    # « six sur dix » mal compris en « 6 novembre » (déjà passé -> 2027)
    res = tools.get_free_slots("6 novembre")
    assert res["ok"] is False and "six mois" in res["erreur"]
    assert tools.get_free_slots("12 février")["ok"] is True
