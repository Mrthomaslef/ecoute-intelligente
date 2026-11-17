"""Relais MacBook → Mac mini par iCloud Drive.

Le MacBook (mode « enregistreur ») ne fait qu'enregistrer : à la fin de l'écoute, le WAV
et une fiche JSON (début, fin, type, taille) sont déposés dans iCloud Drive
› EcouteIntelligente › Relais.
Le Mac mini (mode « traitement », par défaut) surveille ce dossier : transcription finale,
dossier antérieur lu dans Google Drive, CR rédigé par Claude et déposé dans l'Agenda RI.

Le mode d'un Mac se règle dans ~/EcouteIntelligente/.mode (« enregistreur » ou « traitement »).
"""
from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import cli

RELAIS = Path(os.environ.get(
    "ECOUTE_RELAIS",
    Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/EcouteIntelligente/Relais",
))

# iPhone (app Dictaphone) : « Enregistrer dans Fichiers » › iCloud Drive › EcouteIntelligente › iPhone RCP.
# Tout audio déposé ici est traité comme une RCP (jamais déposé dans l'Agenda RI).
DEPOT_IPHONE = Path(os.environ.get("ECOUTE_DEPOT_IPHONE", RELAIS.parent / "iPhone RCP"))


def mode() -> str:
    try:
        m = (cli.DOSSIER / ".mode").read_text().strip().lower()
    except OSError:
        m = ""
    return os.environ.get("ECOUTE_MODE", m) or "traitement"


def est_enregistreur() -> bool:
    return mode() == "enregistreur"


# --------------------------------------------------------------- MacBook
def envoyer(wav: Path, debut: datetime, fin: datetime, type_note: str) -> Path:
    """Copie l'enregistrement dans iCloud (fiche JSON écrite en dernier = copie complète)."""
    RELAIS.mkdir(parents=True, exist_ok=True)
    # Compression AAC (≈6× plus léger que le WAV, intégrée à macOS) : envoi rapide sur réseau lent.
    cible = cli.chemin_libre(RELAIS, f"{debut:%Y-%m-%d_%Hh%M}_{type_note}", ".m4a")
    tmp = RELAIS / f".{cible.name}.partiel"
    conv = subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "48000", "-c", "1",
                           str(wav), str(tmp)], capture_output=True)
    if conv.returncode != 0 or not tmp.exists() or not tmp.stat().st_size:
        cible = cible.with_suffix(".wav")  # repli : WAV d'origine
        shutil.copy2(wav, tmp)
    tmp.rename(cible)
    fiche = {"debut": debut.isoformat(timespec="seconds"), "fin": fin.isoformat(timespec="seconds"),
             "type": type_note, "taille": cible.stat().st_size, "machine": os.uname().nodename}
    (RELAIS / f"{cible.name}.json").write_text(json.dumps(fiche), encoding="utf-8")
    shutil.move(str(wav), cli.chemin_libre(cli.TRAITES, wav.stem, wav.suffix))  # copie locale gardée
    return cible


# --------------------------------------------------------------- Mac mini
SF_DATALESS = 0x40000000  # fichier iCloud dont le contenu n'est pas encore sur ce Mac


def _telecharger(chemin: Path) -> None:
    """iCloud peut ne garder qu'un fichier fantôme (.nom.icloud) : on demande son téléchargement."""
    if not chemin.exists() and (chemin.parent / f".{chemin.name}.icloud").exists():
        subprocess.run(["brctl", "download", str(chemin)], capture_output=True)


def present_localement(chemin: Path) -> bool:
    """Vrai si le contenu du fichier est sur ce Mac. Sinon, demande son téléchargement à iCloud.

    Un fichier « dataless » (coquille vide) ne peut pas être lu par la surveillance lancée en
    arrière-plan (erreur « Resource deadlock avoided ») : on le fait télécharger par iCloud
    (brctl) et on le reprend au passage suivant.
    """
    try:
        if not getattr(chemin.stat(), "st_flags", 0) & SF_DATALESS:
            return True
    except OSError:
        return False
    subprocess.run(["brctl", "download", str(chemin)], capture_output=True)
    return False


DELAI_REPLI = 30 * 60  # s : au-delà, le MacBook traite lui-même ce que le Mac mini n'a pas pris (lent : dernier recours)


def a_traiter(anciennete_min: float = 0) -> list[tuple[Path, dict]]:
    """Enregistrements complets présents dans le relais (audio arrivé en entier).

    anciennete_min : ne garder que ceux déposés depuis au moins ce nombre de secondes.
    """
    prets = []
    if not RELAIS.exists():
        return prets
    for fiche in sorted(RELAIS.glob("*.json")):
        try:
            info = json.loads(fiche.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if anciennete_min and time.time() - fiche.stat().st_mtime < anciennete_min:
            continue
        wav = fiche.with_suffix("")  # x.m4a.json → x.m4a
        _telecharger(wav)
        if not wav.exists() or not present_localement(wav):
            continue
        st = wav.stat()
        taille = info.get("taille")
        complet = st.st_size == int(taille) if taille else time.time() - st.st_mtime > 60
        if complet:
            prets.append((wav, info))
    return prets


def traiter(wav: Path, info: dict) -> Path | None:
    debut = datetime.fromisoformat(info["debut"])
    fin = datetime.fromisoformat(info.get("fin") or info["debut"])
    type_note = info.get("type", "cr")
    fiche = RELAIS / f"{wav.name}.json"
    cli.log(f"Relais : enregistrement de {debut:%Hh%M} reçu du MacBook.")
    brut = cli.transcrire_audio(wav)
    if not brut.strip():
        cli.log("Relais : aucune parole reconnue, enregistrement ignoré.")
        shutil.move(str(wav), cli.chemin_libre(cli.TRAITES, wav.stem, wav.suffix))
        fiche.unlink(missing_ok=True)
        return None
    # Le WAV quitte iCloud dès qu'il est pris en charge (rangé dans 1_Audio/_traites du Mac mini).
    if type_note == "cr":
        sortie = cli.consultation_vers_agenda(brut, wav, debut, fin=fin, poste_local=False)
    elif type_note == "rcp":
        # RCP : traitement complet (son nettoyé, modèle précis, synthèse dossier par dossier).
        from .rcp import rcp_complet
        try:
            sortie = rcp_complet([wav], debut, ouvrir=False)
        except Exception as e:  # noqa: BLE001 — repli : note à relire
            cli.log(f"Synthèse RCP impossible ({e}) : note à relire.")
            sortie = cli.creer_note(brut, type_note, wav)
    else:
        sortie = cli.creer_note(brut, type_note, wav)
    if wav.exists():
        shutil.move(str(wav), cli.chemin_libre(cli.TRAITES, wav.stem, wav.suffix))
    fiche.unlink(missing_ok=True)
    return sortie


# ------------------------------------------------------------ iPhone
def depots_iphone(tailles: dict[Path, int], age_min: float = 60) -> list[Path]:
    """Audios déposés depuis l'iPhone, arrivés en entier (taille stable et âgés d'au moins age_min s)."""
    if not DEPOT_IPHONE.exists():
        return []
    for fantome in DEPOT_IPHONE.glob(".*.icloud"):  # pas encore téléchargé depuis iCloud
        subprocess.run(["brctl", "download", str(DEPOT_IPHONE / fantome.name[1:-len(".icloud")])],
                       capture_output=True)
    return [f for f in cli.fichiers_stables(DEPOT_IPHONE, cli.EXTENSIONS_AUDIO, tailles)
            if time.time() - f.stat().st_mtime >= age_min and present_localement(f)]


def traiter_iphone(audio: Path) -> Path:
    """RCP enregistrée sur l'iPhone : même traitement que la RCP du MacBook (synthèse par dossier)."""
    from .rcp import rcp_complet
    cli.log(f"iPhone : RCP reçue ({audio.stat().st_size // 1_000_000} Mo).")
    sortie = rcp_complet([audio], datetime.fromtimestamp(audio.stat().st_mtime), ouvrir=False)
    if audio.exists():  # l'audio quitte iCloud dès qu'il est traité
        shutil.move(str(audio), cli.chemin_libre(cli.TRAITES, audio.stem, audio.suffix.lower()))
    return sortie


# ------------------------------------------------- écoutes interrompues
INTERROMPU_APRES = 10 * 60  # s sans écriture : l'écoute a été coupée (veille, plantage, bouton oublié)


def ecoutes_interrompues(en_cours: Path, pid: Path, maintenant: float | None = None) -> list[Path]:
    """WAV laissés dans _en_cours par une écoute qui ne s'est pas terminée normalement."""
    maintenant = maintenant or time.time()
    if pid.exists():  # une écoute est en cours : on ne touche à rien
        return []
    return [w for w in sorted(en_cours.glob("*.wav"))
            if w.stat().st_size > 44 and maintenant - w.stat().st_mtime > INTERROMPU_APRES]


def recuperer(wav: Path) -> None:
    """Reprend une écoute interrompue : envoi au Mac mini (enregistreur) ou traitement ici."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{2})h(\d{2})_(cr|rcp)", wav.stem)
    debut = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}", "%Y-%m-%d %H:%M") if m \
        else datetime.fromtimestamp(wav.stat().st_mtime)
    type_note = m.group(4) if m else "cr"
    fin = datetime.fromtimestamp(wav.stat().st_mtime)
    cli.log(f"Écoute interrompue retrouvée ({wav.name}) : reprise automatique.")
    cli.notifier(f"Écoute interrompue de {debut:%Hh%M} récupérée et envoyée au traitement.")
    if est_enregistreur():
        envoyer(wav, debut, fin, type_note)
        return
    # Mac de traitement : on la dépose dans le relais local comme si elle venait du MacBook.
    RELAIS.mkdir(parents=True, exist_ok=True)
    cible = cli.chemin_libre(RELAIS, f"{debut:%Y-%m-%d_%Hh%M}_{type_note}", wav.suffix)
    shutil.move(str(wav), cible)
    (RELAIS / f"{cible.name}.json").write_text(json.dumps(
        {"debut": debut.isoformat(timespec="seconds"), "fin": fin.isoformat(timespec="seconds"),
         "type": type_note, "taille": cible.stat().st_size, "machine": os.uname().nodename}), encoding="utf-8")
