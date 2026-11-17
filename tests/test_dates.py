from datetime import date, datetime, time

import pytest

from lea.dates import (
    DateParseError,
    format_date_fr,
    format_datetime_fr,
    parse_date,
    parse_datetime,
    parse_time,
)

SAMEDI = date(2026, 11, 7)
LUNDI = date(2026, 11, 9)
MERCREDI = date(2026, 11, 11)


@pytest.mark.parametrize(
    "texte, today, attendu",
    [
        ("demain", MERCREDI, date(2026, 11, 12)),
        ("Demain matin", MERCREDI, date(2026, 11, 12)),
        ("après-demain", MERCREDI, date(2026, 11, 13)),
        ("apres demain", MERCREDI, date(2026, 11, 13)),
        ("aujourd'hui", MERCREDI, MERCREDI),
        ("aujourd’hui", MERCREDI, MERCREDI),
        ("cet après-midi", MERCREDI, MERCREDI),
        ("demain après-midi", MERCREDI, date(2026, 11, 12)),
        # fin de mois / d'année
        ("demain", date(2026, 11, 30), date(2026, 12, 1)),
        ("demain", date(2026, 12, 31), date(2027, 1, 1)),
    ],
)
def test_relatifs_simples(texte, today, attendu):
    assert parse_date(texte, today) == attendu


@pytest.mark.parametrize(
    "texte, today, attendu",
    [
        ("lundi prochain", SAMEDI, date(2026, 11, 9)),
        ("lundi prochain", MERCREDI, date(2026, 11, 16)),
        # dit un lundi : le lundi suivant, pas aujourd'hui
        ("lundi prochain", LUNDI, date(2026, 11, 16)),
        ("lundi", LUNDI, date(2026, 11, 16)),
        ("ce vendredi", MERCREDI, date(2026, 11, 13)),
        ("jeudi", MERCREDI, date(2026, 11, 12)),
        ("Mardi prochain", MERCREDI, date(2026, 11, 17)),
        ("lundi en huit", MERCREDI, date(2026, 11, 23)),
        ("mercredi de la semaine prochaine", MERCREDI, date(2026, 11, 18)),
        ("jeudi semaine prochaine", MERCREDI, date(2026, 11, 19)),
        ("la semaine prochaine", MERCREDI, date(2026, 11, 16)),
        ("dans 3 jours", MERCREDI, date(2026, 11, 14)),
        ("dans deux semaines", MERCREDI, date(2026, 11, 25)),
        ("dans une semaine", MERCREDI, date(2026, 11, 18)),
    ],
)
def test_jours_de_la_semaine_et_semaines(texte, today, attendu):
    assert parse_date(texte, today) == attendu


@pytest.mark.parametrize(
    "texte, today, attendu",
    [
        ("2026-11-20", MERCREDI, date(2026, 11, 20)),
        ("20/11", MERCREDI, date(2026, 11, 20)),
        ("20/11/2026", MERCREDI, date(2026, 11, 20)),
        ("05/01", MERCREDI, date(2027, 1, 5)),  # date passée -> année suivante
        ("le 20 novembre", MERCREDI, date(2026, 11, 20)),
        ("1er décembre", MERCREDI, date(2026, 12, 1)),
        ("le premier décembre", MERCREDI, date(2026, 12, 1)),
        ("3 janvier", MERCREDI, date(2027, 1, 3)),
        ("12 février 2027", MERCREDI, date(2027, 2, 12)),
        ("le 20", MERCREDI, date(2026, 11, 20)),
        ("le 2", MERCREDI, date(2026, 12, 2)),  # déjà passé ce mois-ci
        ("mardi 17 novembre", MERCREDI, date(2026, 11, 17)),  # date explicite prioritaire
        ("le 20 à 14h", MERCREDI, date(2026, 11, 20)),
    ],
)
def test_dates_explicites(texte, today, attendu):
    assert parse_date(texte, today) == attendu


@pytest.mark.parametrize("texte", ["", "bientôt", "le 31 novembre", "32/01"])
def test_dates_invalides(texte):
    with pytest.raises(DateParseError):
        parse_date(texte, MERCREDI)


@pytest.mark.parametrize(
    "texte, attendu",
    [
        ("14h", time(14, 0)),
        ("14h30", time(14, 30)),
        ("14 h 30", time(14, 30)),
        ("9:15", time(9, 15)),
        ("à 10 heures", time(10, 0)),
        ("9h et demie", time(9, 30)),
        ("midi", time(12, 0)),
        ("demain", None),
    ],
)
def test_heures(texte, attendu):
    assert parse_time(texte) == attendu


def test_parse_datetime():
    assert parse_datetime("2026-11-16T09:30", MERCREDI) == datetime(2026, 11, 16, 9, 30)
    assert parse_datetime("2026-11-16 9:30", MERCREDI) == datetime(2026, 11, 16, 9, 30)
    assert parse_datetime("demain 14h30", MERCREDI) == datetime(2026, 11, 12, 14, 30)
    assert parse_datetime("lundi prochain à 9h", SAMEDI) == datetime(2026, 11, 9, 9, 0)
    with pytest.raises(DateParseError):
        parse_datetime("demain", MERCREDI)


def test_formatage_pour_reformulation():
    assert format_date_fr(date(2026, 11, 9)) == "lundi 9 novembre 2026"
    assert format_date_fr(date(2026, 12, 1)) == "mardi 1er décembre 2026"
    assert format_datetime_fr(datetime(2026, 8, 3, 9, 30)) == "lundi 3 août 2026 à 9h30"
    assert format_datetime_fr(datetime(2026, 11, 9, 14, 0)) == "lundi 9 novembre 2026 à 14h"
