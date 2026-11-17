import pytest

from lea.voice.speech import pour_la_voix, transcription_utile


@pytest.mark.parametrize("texte, attendu", [
    ("Je vous propose 9h30, 12h ou 16h30.", "Je vous propose 9 heures 30, 12 heures ou 16 heures 30."),
    ("lundi 1er décembre à 1h", "lundi premier décembre à 1 heure"),
    ("Douleur 6/10, c'est noté.", "Douleur 6 sur 10, c'est noté."),
    ("**Bonjour** 😊 !", "Bonjour !"),
    ("le jeudi 12 novembre 2026 à 10h", "le jeudi 12 novembre à 10 heures"),
    ("Votre RDV avec le Dr Martin", "Votre rendez-vous avec le docteur Martin"),
])
def test_pour_la_voix(texte, attendu):
    assert pour_la_voix(texte) == attendu


@pytest.mark.parametrize("texte, utile", [
    ("J'ai mal à une dent", True),
    ("Oui", True),
    ("", False),
    (" . ", False),
    ("Sous-titrage Société Radio-Canada", False),
    ("Merci d'avoir regardé cette vidéo !", False),
    ("Merci.", False),
    ("Merci, au revoir", True),
])
def test_transcription_utile(texte, utile):
    assert transcription_utile(texte) is utile
