import pytest

from lea.telephone import chiffres_en_clair, extraire_telephone, ressemble_a_un_numero


@pytest.mark.parametrize("texte, attendu", [
    ("zéro six, douze, trente-quatre", "0 6 , 12 , 34"),
    ("soixante-dix-huit", "78"),
    ("quatre-vingt-dix-neuf", "99"),
    ("quatre-vingts", "80"),
    ("vingt et un", "21"),
    ("soixante et onze", "71"),
    ("dix-sept", "17"),
])
def test_chiffres_en_clair(texte, attendu):
    assert chiffres_en_clair(texte) == attendu


@pytest.mark.parametrize("texte, attendu", [
    ("Mon numéro est le 06 12 34 56 78.", "06 12 34 56 78"),
    ("0612-3456-78", "06 12 34 56 78"),
    ("+33 6 12 34 56 78", "06 12 34 56 78"),
    ("zéro six, douze, trente-quatre, cinquante-six, soixante-dix-huit", "06 12 34 56 78"),
    ("Zéro, sept, quatre-vingt-dix-neuf, onze, vingt-deux, quarante et un", "07 99 11 22 41"),
    ("zéro six 12 trente-quatre 56 soixante-dix-huit", "06 12 34 56 78"),
    ("C'est le 7647-9566.", "76 47 95 66"),  # 8 chiffres, hors France
    ("Mon numéro est le 75, 75, 50, 75.", "75 75 50 75"),
    ("6, 2, 6, 4, 4, 6, 5, 2, 3, 1.", "62 64 46 52 31"),
    ("Mon numéro de téléphone est le 66 47 99 66 66", "66 47 99 66 66"),
    ("+237 6 77 88 99 00", "+23 76 77 88 99 00"),
    ("J'ai une carie et mon numéro est le 66 47 99 66", "66 47 99 66"),
    ("Le 12 novembre à 10h30, mon numéro est le 66 47 99 66", "66 47 99 66"),
    ("C'est le 76 47", None),                # trop court
    ("Zero, deux, deux, zéro, zéro, deux, deux, zéro.", None),  # 8 chiffres
    ("J'ai mal depuis 8 jours", None),
])
def test_extraire_telephone(texte, attendu):
    assert extraire_telephone(texte) == attendu


def test_ressemble_a_un_numero():
    assert ressemble_a_un_numero("mon numéro est le 75, 75, 50, 75")
    assert ressemble_a_un_numero("Zero, deux, deux, zéro, zéro, deux, deux, zéro.")
    assert not ressemble_a_un_numero("jeudi à 10 heures")
