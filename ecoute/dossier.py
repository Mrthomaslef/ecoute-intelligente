"""Dossier antérieur du patient, déposé dans Google Drive avant la consultation.

Thomas copie le dossier précédent du patient dans
« 1sceenshot et texte com / Consultation JJ/MM » juste avant de le recevoir.
À la fin de l'écoute, on prend le document de ce dossier qui CONCERNE LE PATIENT (son nom
dans le titre ou le texte), on en extrait le texte via rclone, et il sert de contexte au CR.
Aucun document au nom du patient = première consultation (jamais le dossier d'un autre patient).

Le texte est anonymisé localement (comme la transcription) avant tout envoi à Claude.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

RACINE = os.environ.get("ECOUTE_DRIVE", "gdrive:1sceenshot et texte com")
LIMITE_TEXTE = 20000  # caractères gardés au maximum (les plus récents en fin de dossier)
EXTENSIONS_TEXTE = {".txt", ".docx", ".doc", ".rtf", ".odt", ".html", ".htm", ".md"}


@dataclass
class Document:
    id: str
    chemin: str  # relatif à RACINE
    nom: str
    modifie: datetime

    @property
    def titre(self) -> str:
        return Path(self.nom).stem


def rclone() -> str | None:
    for c in (shutil.which("rclone"), str(Path.home() / ".local/bin/rclone"),
              "/opt/homebrew/bin/rclone", "/usr/local/bin/rclone"):
        if c and Path(c).exists():
            return c
    return None


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def est_dossier_du_jour(nom: str, jour: datetime) -> bool:
    """« Consultation 16/11 », « Consultation 16 11 », « consult 16-11-2026 »… (rclone écrit / en ／)."""
    n = _norm(nom)
    if "consult" not in n:
        return False
    return re.search(rf"(?<!\d)0?{jour.day}\D{{1,3}}0?{jour.month}(?!\d)", n) is not None


def _lsjson(chemin: str, *options: str) -> list[dict]:
    exe = rclone()
    if not exe:
        raise RuntimeError("rclone absent (brew install rclone)")
    proc = subprocess.run(
        [exe, "lsjson", f"{RACINE}/{chemin}".rstrip("/"), "--drive-export-formats", "txt",
         "--no-mimetype", *options],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "rclone")
    return json.loads(proc.stdout or "[]")


def _date(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)


def documents_du_jour(jour: datetime) -> list[Document]:
    docs = []
    for d in _lsjson(""):
        if d.get("IsDir") and est_dossier_du_jour(d["Name"], jour):
            for f in _lsjson(d["Name"], "-R", "--files-only"):
                if Path(f["Name"]).suffix.lower() in EXTENSIONS_TEXTE | {".pdf"}:
                    docs.append(Document(f.get("ID") or f["Path"], f"{d['Name']}/{f['Path']}",
                                         f["Name"], _date(f["ModTime"])))
    return docs


def choisir(docs: list[Document], fin: datetime, deja_utilises: set[str]) -> Document | None:
    """Le document le plus récent déposé avant la fin de l'écoute et pas encore utilisé."""
    candidats = [d for d in docs if d.id not in deja_utilises and d.modifie <= fin]
    return max(candidats, key=lambda d: d.modifie, default=None)


def lire(doc: Document) -> str:
    exe = rclone()
    ext = Path(doc.nom).suffix.lower()
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / f"doc{ext}"
        proc = subprocess.run([exe, "copyto", f"{RACINE}/{doc.chemin}", str(local),
                               "--drive-export-formats", "txt"],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0 or not local.exists():
            raise RuntimeError("téléchargement impossible")
        if ext in {".txt", ".md"}:
            texte = local.read_text(encoding="utf-8-sig", errors="replace")
        elif ext == ".pdf":
            if not shutil.which("pdftotext"):
                raise RuntimeError("PDF : pdftotext absent (brew install poppler)")
            texte = subprocess.run(["pdftotext", "-layout", str(local), "-"],
                                   capture_output=True, text=True, timeout=60).stdout
        else:  # docx, rtf, html… : convertisseur intégré à macOS
            subprocess.run(["textutil", "-convert", "txt", "-output", str(local) + ".txt", str(local)],
                           capture_output=True, timeout=60)
            texte = Path(str(local) + ".txt").read_text(encoding="utf-8", errors="replace")
    texte = re.sub(r"\n{3,}", "\n\n", texte).strip()
    return texte[-LIMITE_TEXTE:] if len(texte) > LIMITE_TEXTE else texte


# ----------------------------------------------- documents déjà utilisés
def _registre(dossier: Path) -> Path:
    return dossier / ".dossiers_utilises.json"


def deja_utilises(dossier: Path, jour: datetime) -> set[str]:
    try:
        return set(json.loads(_registre(dossier).read_text()).get(f"{jour:%Y-%m-%d}", []))
    except (OSError, ValueError):
        return set()


def marquer_utilise(dossier: Path, jour: datetime, doc: Document) -> None:
    cle = f"{jour:%Y-%m-%d}"
    try:
        reg = json.loads(_registre(dossier).read_text())
    except (OSError, ValueError):
        reg = {}
    reg = {k: v for k, v in reg.items() if k >= f"{jour.replace(day=1):%Y-%m-%d}"}  # garde le mois
    reg.setdefault(cle, [])
    if doc.id not in reg[cle]:
        reg[cle].append(doc.id)
    _registre(dossier).write_text(json.dumps(reg))


def dossier_anterieur(dossier: Path, debut: datetime, fin: datetime | None = None) -> Document | None:
    """Trouve le document du jour (sans le lire). Lève une exception si Drive est inaccessible."""
    fin = fin or datetime.now()
    return choisir(documents_du_jour(debut), fin, deja_utilises(dossier, debut))


# ------------------------------------------- document rattaché au patient
def concerne(texte: str, mots_patient: list[str]) -> bool:
    """Vrai si un mot du nom du patient (≥ 4 lettres) figure dans le texte (sans accents ni casse)."""
    t = " " + re.sub(r"[^a-z]+", " ", _norm(texte)) + " "
    return any(f" {re.sub(r'[^a-z]+', ' ', _norm(m)).strip()} " in t
               for m in mots_patient if len(m) >= 4)


def document_du_patient(debut: datetime, fin: datetime | None, mots_patient: list[str],
                        docs: list[Document] | None = None, lecteur=None) -> tuple[Document | None, str | None]:
    """Document du jour au nom du patient (titre d'abord, puis texte). (None, None) = première consultation."""
    if not mots_patient:
        return None, None
    fin = fin or datetime.now()
    lecteur = lecteur or lire
    docs = documents_du_jour(debut) if docs is None else docs
    candidats = sorted((d for d in docs if d.modifie <= fin), key=lambda d: d.modifie, reverse=True)
    for d in candidats:
        if concerne(d.titre, mots_patient):
            return d, lecteur(d)
    for d in candidats:
        texte = lecteur(d)
        if concerne(texte, mots_patient):
            return d, texte
    return None, None
