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
# Second canal, indépendant d'iCloud : Google Drive (rclone, remote « gdrive »).
DRIVE_RELAIS = os.environ.get("ECOUTE_DRIVE_RELAIS", "gdrive:01 - Cochin (en cours)/EcouteIntelligente Relais")
INTERVALLE_DRIVE = 30  # s entre deux consultations du dossier Drive (Mac mini)
ATTENTE_NOTE = 3 * 60   # s : on attend une éventuelle note dictée juste après la consultation avant de rédiger
ECART_NOTE = 10 * 60    # s : une note commence moins de 10 min après la fin de la consultation…
DUREE_MAX_NOTE = 3 * 60  # s : … et dure au plus 3 min (au-delà, c'est une autre consultation)
_dernier_drive = [0.0]

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
    envoyer_drive(cible, RELAIS / f"{cible.name}.json")
    shutil.move(str(wav), cli.chemin_libre(cli.TRAITES, wav.stem, wav.suffix))  # copie locale gardée
    return cible


def _rclone() -> str | None:
    from . import dossier
    return dossier.rclone()


def envoyer_drive(audio: Path, fiche: Path) -> bool:
    """Copie aussi l'enregistrement sur Google Drive (si iCloud est bloqué, le Mac mini le prend là)."""
    exe = _rclone()
    if not exe:
        cli.log("Drive : rclone absent, envoi par iCloud seulement.")
        return False
    try:
        for f in (audio, fiche):  # l'audio d'abord, la fiche ensuite (= copie complète)
            r = subprocess.run([exe, "copy", str(f), DRIVE_RELAIS], capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                cli.log(f"Drive : envoi impossible ({(r.stderr.strip().splitlines() or ['?'])[-1]}).")
                return False
        cli.log("Enregistrement envoyé aussi par Google Drive.")
        return True
    except (OSError, subprocess.SubprocessError) as e:
        cli.log(f"Drive : envoi impossible ({e}).")
        return False


# ---------------------------------------------------- registre des reçus
def _registre() -> Path:
    return cli.DOSSIER / ".relais_recus.json"


def deja_recus() -> set[str]:
    try:
        return set(json.loads(_registre().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def marquer_recu(nom: str) -> None:
    noms = deja_recus() | {nom}
    _registre().write_text(json.dumps(sorted(noms)[-500:]), encoding="utf-8")


def recuperer_drive(force: bool = False) -> list[str]:
    """Mac mini : rapatrie dans le relais local les enregistrements arrivés par Google Drive."""
    if not force and time.time() - _dernier_drive[0] < INTERVALLE_DRIVE:
        return []
    _dernier_drive[0] = time.time()
    exe = _rclone()
    if not exe:
        return []
    r = subprocess.run([exe, "lsjson", DRIVE_RELAIS, "--files-only"], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return []
    fichiers = {f["Name"]: f for f in json.loads(r.stdout or "[]")}
    recus, venus = deja_recus(), []
    RELAIS.mkdir(parents=True, exist_ok=True)
    for nom in sorted(fichiers):
        if not nom.endswith(".json"):
            continue
        audio = nom[:-len(".json")]
        if audio in recus or audio not in fichiers or (RELAIS / nom).exists():
            continue
        for f in (audio, nom):  # audio d'abord, fiche ensuite
            subprocess.run([exe, "copyto", f"{DRIVE_RELAIS}/{f}", str(RELAIS / f)],
                           capture_output=True, timeout=600, check=True)
        cli.log(f"Drive : {audio} récupéré.")
        venus.append(audio)
    return venus


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
        # Ancienneté comptée depuis l'ARRIVÉE sur ce Mac (ctime) : rclone/iCloud conservent la date d'origine
        # (mtime), ce qui faisait traiter une consultation avant l'arrivée de la note dictée juste après.
        st_f = fiche.stat()
        if anciennete_min and time.time() - max(st_f.st_mtime, st_f.st_ctime) < anciennete_min:
            continue
        wav = fiche.with_suffix("")  # x.m4a.json → x.m4a
        if wav.name in deja_recus():  # déjà reçu par l'autre canal : doublon écarté (rien n'est supprimé)
            doublons = cli.AUDIO / "_doublons"
            doublons.mkdir(parents=True, exist_ok=True)
            for f in (wav, fiche):
                if f.exists():
                    shutil.move(str(f), cli.chemin_libre(doublons, f.stem, f.suffix))
            continue
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
    marquer_recu(wav.name)
    if type_note == "test":  # essai de bout en bout du relais : rien n'est transcrit ni déposé
        cli.log(f"Relais : TEST reçu ({wav.name}, {wav.stat().st_size} o) — chaîne MacBook → Mac mini OK.")
        cli.notifier("Test du relais reçu sur le Mac mini.")
        shutil.move(str(wav), cli.chemin_libre(cli.TRAITES, wav.stem, wav.suffix))
        fiche.unlink(missing_ok=True)
        return None
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


# ------------------------------------------------ consultation + note finale
def grouper(prets: list[tuple[Path, dict]]) -> list[list[tuple[Path, dict]]]:
    """Regroupe une consultation et la (ou les) note(s) dictée(s) juste après (même patient, même moment)."""
    def bornes(info):
        d = datetime.fromisoformat(info["debut"])
        return d, datetime.fromisoformat(info.get("fin") or info["debut"])
    groupes: list[list[tuple[Path, dict]]] = []
    for item in sorted(prets, key=lambda p: p[1]["debut"]):
        if item[1].get("type", "cr") != "cr":
            groupes.append([item])
            continue
        prec = groupes[-1] if groupes and groupes[-1][-1][1].get("type", "cr") == "cr" else None
        debut_i, fin_i = bornes(item[1])
        est_note = (fin_i - debut_i).total_seconds() <= DUREE_MAX_NOTE
        if prec and est_note and (debut_i - bornes(prec[-1][1])[1]).total_seconds() <= ECART_NOTE:
            prec.append(item)
        else:
            groupes.append([item])
    return groupes


def traiter_groupe(groupe: list[tuple[Path, dict]]) -> Path | None:
    """Une consultation suivie de notes dictées : une seule transcription, un seul CR."""
    if len(groupe) == 1:
        return traiter(*groupe[0])
    premier, info0 = groupe[0]
    debut = datetime.fromisoformat(info0["debut"])
    fin = datetime.fromisoformat(groupe[-1][1].get("fin") or groupe[-1][1]["debut"])
    cli.log(f"Relais : consultation de {debut:%Hh%M} + {len(groupe) - 1} note(s) dictée(s) à la suite, réunies en un seul CR.")
    parties = []
    for i, (wav, info) in enumerate(groupe):
        marquer_recu(wav.name)
        texte = cli.transcrire_audio(wav).strip()
        if i == 0:
            parties.append(texte)
        elif texte:
            parties.append("--- NOTE DICTÉE PAR LE MÉDECIN APRÈS LA CONSULTATION (prioritaire pour la synthèse, "
                           "la décision et la conduite à tenir) ---\n" + texte)
    brut = "\n\n".join(p for p in parties if p)
    sortie = cli.consultation_vers_agenda(brut, premier, debut, fin=fin, poste_local=False) if brut.strip() else None
    for wav, _ in groupe:
        if wav.exists():
            shutil.move(str(wav), cli.chemin_libre(cli.TRAITES, wav.stem, wav.suffix))
        (RELAIS / f"{wav.name}.json").unlink(missing_ok=True)
    return sortie


def arrive_depuis(wav: Path) -> float:
    """Secondes depuis l'arrivée de la fiche sur ce Mac (ctime ; la date d'origine est conservée par rclone/iCloud)."""
    try:
        st = (wav.parent / f"{wav.name}.json").stat()
        return time.time() - max(st.st_mtime, st.st_ctime)
    except OSError:
        return 0.0


def groupes_prets(attente: float) -> list[list[tuple[Path, dict]]]:
    """Groupes (consultation + notes) dont TOUS les éléments sont arrivés depuis au moins `attente` s.

    Le regroupement se fait sur tout ce qui est présent, même arrivé à l'instant : une consultation
    n'est donc jamais traitée seule si sa note dictée vient d'arriver.
    """
    return [g for g in grouper(a_traiter()) if all(arrive_depuis(w) >= attente for w, _ in g)]
