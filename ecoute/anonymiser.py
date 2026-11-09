"""Anonymisation locale d'une transcription médicale (aucun appel réseau).

Remplace les identifiants directs (noms précédés d'une civilité, dates complètes,
numéros IPP/NIP/INS, téléphones, e-mails, noms d'une liste locale) par des balises.
Repère aussi les mots en majuscule suspects, à vérifier lors de la relecture humaine.
L'anonymisation automatique n'est PAS infaillible : la relecture reste obligatoire.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

MOIS = (
    "janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
    "septembre|octobre|novembre|décembre|decembre"
)
MAJ = r"[A-ZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸ]"
MIN = r"[a-zà-öø-ÿœ'’\-]"
MOT_NOM = rf"{MAJ}(?:{MIN}+|{MAJ}+)(?:-{MAJ}{MIN}+)?"
SUITE_NOMS = rf"{MOT_NOM}(?:\s+(?:de\s+|du\s+|le\s+|la\s+)?{MOT_NOM}){{0,2}}"

CIVILITES = (
    r"Monsieur|Madame|Mademoiselle|M\.|Mme\.?|Mlle\.?|Mr\.?|"
    r"Docteur|Dr\.?|Professeur|Pr\.?|Maître|Me\."
)

# Nombres dictés que Whisper écrit en lettres (« 8 milliards 12 millions 345 mille 678 »).
NB = (
    r"(?:\d+|zéro|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|"
    r"treize|quatorze|quinze|seize|vingts?|trente|quarante|cinquante|soixante|cent|cents|"
    r"mille|millions?|milliards?|et)"
)
SUITE_NB = rf"{NB}(?:[\s,.-]+{NB})*"
CLES_NUMERO = (
    r"IPP?|NIP|INS|NIR|n°\s*(?:de\s+)?dossier|numéro\s+(?:de\s+)?(?:dossier|patient|sécu\w*|"
    r"sécurité\s+sociale|IPP|NIP|INS)|sécu(?:rité\s+sociale)?"
)

# (étiquette, motif, remplacement) — appliqués dans l'ordre.
REGLES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "NUMERO",
        re.compile(rf"\b({CLES_NUMERO})\b(\s*(?:est|:|,)?\s*){SUITE_NB}", re.IGNORECASE),
        r"\1\2[NUMERO]",
    ),
    (
        "NUMERO",
        re.compile(rf"\b{SUITE_NB}\s+(?:milliards?|millions?)(?:[\s,.-]+{NB})*", re.IGNORECASE),
        "[NUMERO]",
    ),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    (
        "TEL",
        re.compile(r"(?<!\d)(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}(?!\d)"),
        "[TEL]",
    ),
    # IPP / NIP / INS / NIR : toute suite de 7 chiffres ou plus (espaces tolérés)
    ("NUMERO", re.compile(r"(?<!\d)\d(?:[\s.]?\d){6,}(?!\d)"), "[NUMERO]"),
    (
        "DATE",
        re.compile(r"\b\d{1,2}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*(?:19|20)?\d{2}\b"),
        "[DATE]",
    ),
    (
        "DATE",
        re.compile(
            rf"\b(?:1er|\d{{1,2}})\s+(?:{MOIS})\s+(?:19|20)\d{{2}}\b", re.IGNORECASE
        ),
        "[DATE]",
    ),
    (
        "DATE",
        re.compile(rf"\b(n[ée]e?s?\s+(?:le\s+|en\s+)?)(?:\d{{1,2}}\s+(?:{MOIS})\s+)?(?:19|20)\d{{2}}\b", re.IGNORECASE),
        r"\1[DATE]",
    ),
    (
        "NOM",
        re.compile(rf"\b({CIVILITES})\s+{SUITE_NOMS}"),
        r"\1 [NOM]",
    ),
    (
        "NOM",
        re.compile(
            rf"\b((?:s'appelle|s’appelle|nommée?|du nom de|prénommée?)\s+){SUITE_NOMS}",
            re.IGNORECASE,
        ),
        r"\1[NOM]",
    ),
]

# Mots capitalisés fréquents et non identifiants (pour limiter les fausses alertes).
MOTS_SURS = {
    "IRM", "TDM", "TEP", "PET", "RCP", "CR", "RI", "AP-HP", "Cochin", "Paris",
    "France", "Achard", "Ollier", "Doppler", "Gadolinium", "Lipiodol",
    "Monsieur", "Madame", "Docteur", "Professeur", "Patient", "Patiente",
    "Merci", "Bonjour", "Alors", "Donc", "Oui", "Non", "Ok", "OK", "Euh",
    "Ewing", "Paget", "Hodgkin", "Kahler", "Brodie", "Charcot", "Tietze",
    "Morel-Lavallée", "Segond", "Hoffa", "Baker", "Hill-Sachs", "Bankart",
    "Modic", "Kellgren", "Lawrence", "Enneking", "Mirels", "Tokuhashi",
    "Tomita", "SINS", "Spinal", "Bosniak", "Lequesne", "Mayo", "TNM",
    "Xarelto", "Eliquis", "Kardegic", "Plavix", "Previscan", "Lovenox",
    "Préviscan", "Kardégic", "Coumadine", "Pradaxa", "Brilique", "Efient",
}

MASQUE_BALISE = re.compile(r"\[[A-Z]+\]")


@dataclass
class Resultat:
    texte: str
    remplacements: Counter = field(default_factory=Counter)
    a_verifier: list[str] = field(default_factory=list)


def charger_liste_noms(chemin: Path | None) -> list[str]:
    if not chemin or not chemin.exists():
        return []
    noms = []
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if ligne and not ligne.startswith("#"):
            noms.append(ligne)
    return noms


def mots_suspects(texte: str) -> list[str]:
    """Mots capitalisés hors début de phrase et hors liste sûre."""
    suspects: dict[str, None] = {}
    for m in re.finditer(rf"(?<![.!?:\n]\s)(?<!^)\b{MOT_NOM}\b", texte):
        mot = m.group(0)
        debut = texte[max(0, m.start() - 2): m.start()]
        if m.start() == 0 or re.search(r"[.!?:\n]\s*$", debut):
            continue
        if mot in MOTS_SURS or mot.isupper() and len(mot) <= 5:
            continue
        suspects.setdefault(mot, None)
    return list(suspects)


def anonymiser(texte: str, noms_locaux: list[str] | None = None) -> Resultat:
    res = Resultat(texte=texte)
    for nom in sorted(noms_locaux or [], key=len, reverse=True):
        motif = re.compile(rf"(?<!\w){re.escape(nom)}(?!\w)", re.IGNORECASE)
        res.texte, n = motif.subn("[NOM]", res.texte)
        if n:
            res.remplacements["NOM"] += n
    for etiquette, motif, remplacement in REGLES:
        res.texte, n = motif.subn(remplacement, res.texte)
        if n:
            res.remplacements[etiquette] += n
    res.a_verifier = mots_suspects(res.texte)
    return res
