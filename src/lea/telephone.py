"""Numéros de téléphone dictés : « zéro six, douze, trente-quatre… » -> 06 12 34 …"""

from __future__ import annotations

import re

from lea.dates import normaliser

UNITES = {
    "zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
    "six": 6, "sept": 7, "huit": 8, "neuf": 9,
    # erreurs fréquentes de transcription
    "zeros": 0, "cinque": 5, "sinq": 5, "cink": 5, "sis": 6, "set": 7, "uit": 8,
}
DIX_A_SEIZE = {
    "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
}
DIZAINES = {"vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60}


def _lire_nombre(mots: list[str], i: int) -> tuple[int | None, int]:
    """Lit un nombre de 0 à 99 écrit en lettres à partir de mots[i].
    Renvoie (valeur, nombre de mots consommés) ou (None, 0)."""
    m = mots[i]
    suivant = mots[i + 1] if i + 1 < len(mots) else ""

    def dix_a_dix_neuf(j: int) -> tuple[int | None, int]:
        if j >= len(mots):
            return None, 0
        if mots[j] == "dix" and j + 1 < len(mots) and mots[j + 1] in ("sept", "huit", "neuf"):
            return 10 + UNITES[mots[j + 1]], 2
        if mots[j] in DIX_A_SEIZE:
            return DIX_A_SEIZE[mots[j]], 1
        return None, 0

    def unite_apres(j: int) -> tuple[int, int]:
        """Unité éventuelle après une dizaine (« et un », « deux »…)."""
        if j < len(mots) and mots[j] == "et" and j + 1 < len(mots) and mots[j + 1] in ("un", "une"):
            return 1, 2
        if j < len(mots) and mots[j] in UNITES and UNITES[mots[j]] > 0:
            return UNITES[mots[j]], 1
        return 0, 0

    # quatre-vingt(s) [dix-neuf | trois]
    if m == "quatre" and suivant in ("vingt", "vingts"):
        v, n = dix_a_dix_neuf(i + 2)
        if v is not None:
            return 80 + v, 2 + n
        u, n = unite_apres(i + 2)
        return 80 + u, 2 + n
    # soixante [et onze | dix-huit | cinq]
    if m == "soixante":
        j = i + 1
        if j < len(mots) and mots[j] == "et" and j + 1 < len(mots) and mots[j + 1] == "onze":
            return 71, 3
        v, n = dix_a_dix_neuf(j)
        if v is not None:
            return 60 + v, 1 + n
        u, n = unite_apres(j)
        return 60 + u, 1 + n
    if m in DIZAINES:
        u, n = unite_apres(i + 1)
        return DIZAINES[m] + u, 1 + n
    v, n = dix_a_dix_neuf(i)
    if v is not None:
        return v, n
    if m in UNITES:
        return UNITES[m], 1
    return None, 0


def chiffres_en_clair(texte: str) -> str:
    """Remplace les nombres écrits en lettres par des chiffres.

    « zéro six, douze, trente-quatre » -> « 0 6 12 34 »
    """
    mots = re.findall(r"[a-z]+|\d+|[^\sa-z\d]", normaliser(texte).replace("-", " "))
    sortie, i = [], 0
    while i < len(mots):
        valeur, n = _lire_nombre(mots, i)
        if mots[i] in ("un", "une"):
            suivant = mots[i + 1] if i + 1 < len(mots) else ""
            precedent_nombre = bool(sortie) and sortie[-1].isdigit()
            suivant_nombre = suivant.isdigit() or _lire_nombre(mots, i + 1)[0] is not None if suivant else False
            if not (precedent_nombre or suivant_nombre):
                valeur = None
        if valeur is None:
            sortie.append(mots[i])
            i += 1
        else:
            sortie.append(str(valeur))
            i += n
    return " ".join(sortie)


def valider_telephone(brut: str) -> str | None:
    """Numéro valide au format lisible, ou None. Tous les pays sont acceptés :

    - international (« +237… », « 00237… ») : 8 à 15 chiffres ;
    - commençant par 0 : exactement 10 chiffres (France, Côte d'Ivoire…) ;
    - autres numéros locaux (« 66 47 99 66 ») : 8 à 12 chiffres.
    """
    brut = str(brut or "").strip()
    chiffres = re.sub(r"\D", "", brut)
    international = brut.startswith("+") or chiffres.startswith("00")
    if chiffres.startswith("00"):
        chiffres = chiffres[2:]
    if chiffres.startswith("33") and len(chiffres) == 11:  # France : +33 6… -> 06…
        chiffres, international = "0" + chiffres[2:], False
    if international:
        return "+" + formater_chiffres(chiffres) if 8 <= len(chiffres) <= 15 else None
    if chiffres.startswith("0"):
        return formater_chiffres(chiffres) if len(chiffres) == 10 else None
    return formater_chiffres(chiffres) if 8 <= len(chiffres) <= 12 else None


def extraire_telephone(texte: str) -> str | None:
    """Numéro trouvé dans le texte (chiffres ou lettres), ou None.
    Les heures, dates et durées de la phrase ne sont pas comptées."""
    prefixe = "+" if "+" in str(texte or "") else ""
    return valider_telephone(prefixe + _chiffres_utiles(texte))


# Nombres qui ne font pas partie d'un numéro : heures, dates, durées, douleur.
_MOIS = ("janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre")
_PAS_UN_NUMERO = re.compile(
    rf"\b\d+\s*(?:heures?|h|:)\s*\d*"
    rf"|\b\d+\s+(?:{_MOIS})(?:\s+\d{{4}})?"
    rf"|\b\d+\s+sur\s+\d+"
    rf"|\b\d+\s*(?:jours?|semaines?|mois|ans?|minutes?)\b"
)
_MOTS_DE_LIAISON = {
    "mon", "ma", "numero", "de", "du", "telephone", "tel", "portable", "est", "le", "la", "c",
    "ce", "et", "alors", "donc", "ensuite", "puis", "voila", "oui", "euh", "pardon", "suite",
    "fin", "sont", "les", "derniers", "chiffres", "s", "c'est", "cest",
}


def _chiffres_utiles(texte: str) -> str:
    return re.sub(r"\D", "", _PAS_UN_NUMERO.sub(" ", chiffres_en_clair(texte)))


def ressemble_a_un_numero(texte: str) -> bool:
    """Le patient semble avoir dicté un numéro (au moins 6 chiffres), même incomplet.
    Les heures, dates et durées (« le 12 novembre à 10h30 ») ne comptent pas."""
    return len(_chiffres_utiles(texte)) >= 6


def est_une_dictee(texte: str) -> bool:
    """La phrase ne contient que des chiffres (et des mots de liaison) : morceau de numéro."""
    mots = re.findall(r"[a-z']+|\d+", chiffres_en_clair(texte))
    chiffres = [m for m in mots if m.isdigit()]
    return bool(chiffres) and all(m.isdigit() or m in _MOTS_DE_LIAISON for m in mots)


def formater_chiffres(chiffres: str) -> str:
    return " ".join(chiffres[k:k + 2] for k in range(0, len(chiffres), 2))


def telephone_en_morceaux(messages: list[str]) -> tuple[str | None, str | None]:
    """Cherche un numéro dicté en plusieurs fois (le détecteur de silence coupe
    souvent la dictée en plusieurs phrases).

    `messages` : phrases du patient, de la plus ancienne à la plus récente.
    Renvoie (numéro complet, début de numéro en cours), l'un ou l'autre ou aucun.
    Le numéro retenu est le plus long formé par les dernières phrases de dictée.
    """
    morceaux: list[str] = []
    for texte in reversed(messages):
        if not est_une_dictee(texte) or len(morceaux) >= 6:
            break
        morceaux.insert(0, _chiffres_utiles(texte))
    for i in range(len(morceaux)):
        if tel := valider_telephone("".join(morceaux[i:])):
            return tel, None
    # Début de numéro : les chiffres récents, tant qu'ils restent assez courts.
    for i in range(len(morceaux)):
        debut = "".join(morceaux[i:])
        if 2 <= len(debut) < (10 if debut.startswith("0") else 8):
            return None, debut
    return None, None
