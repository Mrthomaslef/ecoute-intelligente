"""CR validés par le Dr Lefevre, servant de modèles pour les CR suivants.

À la fin de chaque consultation, Thomas colle le CR validé dans le document
« CR consultation » du dossier du jour (Drive › 1sceenshot et texte com › Consultation JJ/MM).
Avant chaque rédaction, on relit ces documents (cache local : seuls les documents modifiés sont
relus), on découpe les lettres, on les anonymise localement et on joint les plus récentes à la
consigne comme exemples de style, de longueur et de concision.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import dossier

NB_EXEMPLES = 3
LONGUEUR_MIN, LONGUEUR_MAX = 300, 2500

_DEBUT_LETTRE = re.compile(r"(?=^\s*(?:Cher|Chère)\s+(?:confrère|consœur|consoeur)\b)", re.I | re.M)
_CIVILITE_NOM = re.compile(
    r"\b(Madame|Monsieur|Mme|Mlle|Mr|M\.)\s+((?:[A-ZÀ-ÖØ-Ý][\w'’\-]*\s*){1,5})")
_DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
_IPP = re.compile(r"\b80\d{8}\b")


def est_cr_valide(nom: str) -> bool:
    """« CR consultation », « CR consultations 16-11 »… (pas les CR pré-op / op / post-op)."""
    return dossier._norm(Path(nom).stem).startswith("cr consult")


def decouper_lettres(texte: str) -> list[str]:
    lettres = []
    for bloc in _DEBUT_LETTRE.split(texte.replace("\r", "")):
        bloc = re.sub(r"\n{3,}", "\n\n", bloc).strip()
        if LONGUEUR_MIN <= len(bloc) <= LONGUEUR_MAX and re.match(r"(?i)\s*ch[eè]re?", bloc):
            lettres.append(bloc)
    return lettres


def masquer(lettre: str) -> str:
    """Masquage local de l'identité (le corps clinique est conservé)."""
    lettre = _CIVILITE_NOM.sub(lambda m: "[PATIENT] ", lettre)
    lettre = _DATE.sub("[DATE]", lettre)
    return _IPP.sub("[NUMERO]", lettre)


def _cache(dossier_local: Path) -> Path:
    return dossier_local / ".modeles_valides.json"


def rafraichir(dossier_local: Path, docs: list[dossier.Document] | None = None, lecteur=None) -> list[dict]:
    """Met à jour le cache des lettres validées et renvoie [{modifie, lettres}], du plus récent au plus ancien."""
    try:
        cache = json.loads(_cache(dossier_local).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if docs is None:
        docs = [dossier.Document(f.get("ID") or f["Path"], f["Path"], f["Name"], dossier._date(f["ModTime"]))
                for f in dossier._lsjson("", "-R", "--files-only") if est_cr_valide(f["Name"])]
    lecteur = lecteur or dossier.lire
    neuf = {}
    for d in docs:
        if not est_cr_valide(d.nom):
            continue
        cle, date = d.id, d.modifie.isoformat()
        if cle in cache and cache[cle].get("modifie") == date:
            neuf[cle] = cache[cle]
        else:
            neuf[cle] = {"modifie": date, "lettres": [masquer(l) for l in decouper_lettres(lecteur(d))]}
    _cache(dossier_local).write_text(json.dumps(neuf, ensure_ascii=False), encoding="utf-8")
    return sorted(neuf.values(), key=lambda v: v["modifie"], reverse=True)


def exemples(dossier_local: Path, n: int = NB_EXEMPLES, **kw) -> list[str]:
    """Les n dernières lettres validées (les plus récentes d'abord)."""
    sortie = []
    for doc in rafraichir(dossier_local, **kw):
        for lettre in reversed(doc["lettres"]):  # dans un document, la dernière lettre est la plus récente
            if lettre not in sortie:
                sortie.append(lettre)
            if len(sortie) >= n:
                return sortie
    return sortie
