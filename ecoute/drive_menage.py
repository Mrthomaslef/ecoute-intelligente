"""Ménage du dossier de dépôt Drive « 1sceenshot et texte com » + dossiers du jour.

Règles (sans rien casser) :
- les dossiers / fichiers DATÉS (« Consultation 16/11 », « CR OP 03/11 », « Echo 13/11 »,
  « RCP 16/11 », « Consultation 09/11.docx »…) antérieurs à aujourd'hui sont déplacés dans
  « <Mois> archives » (ex. « Novembre archives »), À L'INTÉRIEUR du même dossier : les scripts
  qui cherchent les « CR consultation » en récursif (modèles de CR) les retrouvent toujours ;
- rien n'est renommé, supprimé ni modifié ; un nom déjà présent dans l'archive n'est jamais écrasé
  (l'élément reste alors en place et c'est signalé) ;
- les éléments non datés (« IRM novembre », trames de CR, archives) ne bougent pas ;
- crée enfin les dossiers du jour « Consultation JJ/MM » et « RCP JJ/MM » s'ils manquent.

Usage : ecoute drive [--essai]   (--essai : affiche sans rien déplacer)
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from datetime import datetime

from . import dossier

MOIS = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre",
        "Octobre", "Novembre", "Décembre"]
DATE = re.compile(r"(?<!\d)(\d{1,2})\s*[／/\-. ]\s*(\d{1,2})(?:\s*[／/\-. ]\s*(\d{2,4}))?(?!\d)")
JAMAIS = re.compile(r"archives?$|^mod[eè]les", re.IGNORECASE)  # dossiers de rangement


def date_du_nom(nom: str, aujourd_hui: datetime) -> datetime | None:
    if JAMAIS.search(nom.rsplit(".", 1)[0].strip()):
        return None
    m = DATE.search(nom)
    if not m:
        return None
    j, mo = int(m.group(1)), int(m.group(2))
    an = int(m.group(3)) if m.group(3) else aujourd_hui.year
    an += 2000 if an < 100 else 0
    try:
        d = datetime(an, mo, j)
    except ValueError:
        return None
    if not m.group(3) and d > aujourd_hui:  # « 28/12 » vu en janvier : année précédente
        d = d.replace(year=an - 1)
    return d


def _rclone(*args: str) -> str:
    p = subprocess.run([dossier.rclone(), *args], capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip().splitlines()[-1] if p.stderr.strip() else "rclone")
    return p.stdout


def _lister(chemin: str) -> list[dict]:
    """Noms RÉELS (sans extension d'export des Google Docs) et identifiants Drive."""
    return json.loads(_rclone("lsjson", f"{dossier.RACINE}/{chemin}".rstrip("/"), "--no-mimetype",
                              "--drive-skip-gdocs=false", "--drive-export-formats", "") or "[]")


def _id_racine() -> str:
    parent, nom = dossier.RACINE.rsplit(":", 1)[0] + ":", dossier.RACINE.split(":", 1)[1]
    for e in json.loads(_rclone("lsjson", parent, "--dirs-only", "--no-mimetype")):
        if e["Name"] == nom:
            return e["ID"]
    raise RuntimeError("dossier racine introuvable")


def _jeton() -> str:
    _rclone("about", dossier.RACINE.split(":", 1)[0] + ":")  # rafraîchit le jeton si besoin
    conf = json.loads(_rclone("config", "dump"))[dossier.RACINE.split(":", 1)[0]]
    return json.loads(conf["token"])["access_token"]


def _deplacer(id_: str, de: str, vers: str, jeton: str) -> None:
    """Change seulement le dossier parent (même fichier, même identifiant, aucune conversion)."""
    url = (f"https://www.googleapis.com/drive/v3/files/{id_}?addParents={vers}&removeParents={de}"
           "&fields=id,parents&supportsAllDrives=true")
    req = urllib.request.Request(url, method="PATCH", data=b"{}",
                                 headers={"Authorization": f"Bearer {jeton}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        if vers not in json.loads(r.read()).get("parents", []):
            raise RuntimeError("déplacement non confirmé par Drive")


def menage(aujourd_hui: datetime | None = None, essai: bool = False) -> list[str]:
    jour = (aujourd_hui or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    bilan: list[str] = []
    racine = _lister("")
    noms = {e["Name"] for e in racine}
    ids = {e["Name"]: e["ID"] for e in racine}
    contenus: dict[str, set[str]] = {}
    id_racine = jeton = None
    for e in sorted(racine, key=lambda e: e["Name"]):
        d = date_du_nom(e["Name"], jour)
        if d is None or d >= jour:
            continue
        archive = f"{MOIS[d.month - 1]} archives" + ("" if d.year == jour.year else f" {d.year}")
        if archive not in contenus:
            contenus[archive] = ({x["Name"] for x in _lister(archive)} if archive in noms else set())
        if e["Name"] in contenus[archive]:
            bilan.append(f"⚠️ laissé en place (déjà dans {archive}) : {e['Name']}")
            continue
        bilan.append(f"→ {archive} : {e['Name']}")
        if not essai:
            if archive not in noms:
                _rclone("mkdir", f"{dossier.RACINE}/{archive}")
                noms.add(archive)
                ids[archive] = next(x["ID"] for x in _lister("") if x["Name"] == archive)
            id_racine = id_racine or _id_racine()
            jeton = jeton or _jeton()
            _deplacer(e["ID"], id_racine, ids[archive], jeton)
        contenus[archive].add(e["Name"])
    for nom in (f"Consultation {jour:%d}／{jour:%m}", f"RCP {jour:%d}／{jour:%m}"):
        if nom not in noms:
            bilan.append(f"+ créé : {nom}")
            if not essai:
                _rclone("mkdir", f"{dossier.RACINE}/{nom}")
    return bilan


def main(essai: bool = False) -> None:
    for ligne in menage(essai=essai) or ["Rien à ranger, dossiers du jour déjà présents."]:
        print(ligne.replace("／", "/"))
