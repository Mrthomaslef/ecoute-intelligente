"""Écoute intelligente — enregistrement → transcription locale → anonymisation →
relecture humaine → rédaction par Claude.

Circuit des dossiers (par défaut ~/EcouteIntelligente, hors iCloud) :
  1_Audio/CR, 1_Audio/RCP  : déposer les enregistrements (.m4a, .mp3, .wav…)
  2_A_Relire               : transcriptions anonymisées, À RELIRE et corriger
  3_Valide                 : déplacer ici une note relue → envoi à Claude
  4_CR_Prets               : comptes-rendus, actions et brouillons de mails
Rien ne part vers Claude tant qu'une note n'a pas été déplacée dans 3_Valide.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .anonymiser import anonymiser, charger_liste_noms

RACINE_PROJET = Path(__file__).resolve().parents[1]
DOSSIER = Path(os.environ.get("ECOUTE_DOSSIER", Path.home() / "EcouteIntelligente"))
AUDIO, A_RELIRE, VALIDE, PRETS = (
    DOSSIER / "1_Audio", DOSSIER / "2_A_Relire", DOSSIER / "3_Valide", DOSSIER / "4_CR_Prets"
)
TRAITES = AUDIO / "_traites"
ENVOYES = VALIDE / "_envoyes"
LISTE_NOMS = DOSSIER / "noms_a_masquer.txt"

MODELE_WHISPER = os.environ.get("ECOUTE_WHISPER", "mlx-community/whisper-large-v3-turbo")
MODELE_CLAUDE = os.environ.get("ECOUTE_MODELE", "sonnet")
EXTENSIONS_AUDIO = {".m4a", ".mp3", ".wav", ".aac", ".mp4", ".mov", ".caf", ".ogg", ".flac", ".webm"}
VOCABULAIRE = (
    "Radiologie ostéo-articulaire et interventionnelle, Hôpital Cochin. RCP sarcomes. "
    "IRM, scanner, TEP, biopsie, cryoablation, radiofréquence, cimentoplastie, vissage "
    "percutané, électrochimiothérapie, sclérothérapie, ostéome ostéoïde, chondrosarcome, "
    "tumeur à cellules géantes, kyste anévrismal, métastase, anticoagulants, Xarelto, Eliquis."
)


def initialiser() -> None:
    for d in (AUDIO / "CR", AUDIO / "RCP", TRAITES, A_RELIRE, VALIDE, ENVOYES, PRETS):
        d.mkdir(parents=True, exist_ok=True)
    if not LISTE_NOMS.exists():
        LISTE_NOMS.write_text(
            "# Un nom par ligne : noms de patients, proches ou lieux à toujours masquer.\n"
            "# Ce fichier reste sur ce Mac et n'est jamais envoyé.\n",
            encoding="utf-8",
        )


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- notes .md
def ecrire_note(chemin: Path, entete: dict[str, str], corps: str) -> None:
    lignes = ["---", *(f"{k}: {v}" for k, v in entete.items()), "---", "", corps.strip(), ""]
    chemin.write_text("\n".join(lignes), encoding="utf-8")


def lire_note(chemin: Path) -> tuple[dict[str, str], str]:
    texte = chemin.read_text(encoding="utf-8")
    entete: dict[str, str] = {}
    if texte.startswith("---\n"):
        fin = texte.find("\n---", 4)
        if fin != -1:
            for ligne in texte[4:fin].splitlines():
                if ":" in ligne:
                    k, v = ligne.split(":", 1)
                    entete[k.strip()] = v.split("#")[0].strip()
            texte = texte[fin + 4:]
    # Les lignes « > » sont les consignes de relecture : jamais envoyées.
    corps = "\n".join(l for l in texte.splitlines() if not l.startswith(">"))
    return entete, corps.strip()


def chemin_libre(dossier: Path, base: str, ext: str) -> Path:
    p, i = dossier / f"{base}{ext}", 2
    while p.exists():
        p, i = dossier / f"{base}_{i}{ext}", i + 1
    return p


# ---------------------------------------------------------- transcription
def transcrire_audio(audio: Path) -> str:
    try:
        import mlx_whisper
    except ImportError:
        sys.exit("mlx-whisper absent : lancez ./install.sh")
    log(f"Transcription locale ({MODELE_WHISPER.split('/')[-1]})…")
    res = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=MODELE_WHISPER,
        language="fr",
        initial_prompt=VOCABULAIRE,
        condition_on_previous_text=False,
    )
    segments = [s["text"].strip() for s in res.get("segments", []) if s["text"].strip()]
    return "\n".join(segments) if segments else res.get("text", "").strip()


def deviner_type(audio: Path) -> str:
    if audio.parent.name.upper() in {"CR", "RCP"}:
        return audio.parent.name.lower()
    return "rcp" if "rcp" in audio.stem.lower() else "cr"


def traiter_audio(audio: Path, type_note: str | None = None) -> Path:
    type_note = type_note or deviner_type(audio)
    brut = transcrire_audio(audio)
    res = anonymiser(brut, charger_liste_noms(LISTE_NOMS))
    # Nom de fichier horodaté : le nom de l'audio peut contenir un nom de patient.
    base = f"{datetime.now():%Y-%m-%d_%Hh%M}_{type_note}"
    note = chemin_libre(A_RELIRE, base, ".md")
    remplacements = ", ".join(f"{k}×{v}" for k, v in sorted(res.remplacements.items())) or "aucun"
    consignes = [
        "> **À RELIRE AVANT ENVOI.** Corrigez la transcription et effacez tout nom, date de",
        "> naissance ou numéro restant. Puis déplacez ce fichier dans 3_Valide.",
    ]
    if res.a_verifier:
        consignes.append("> Mots à vérifier (noms propres possibles) : " + ", ".join(res.a_verifier))
    entete = {
        "type": f"{type_note}  # cr ou rcp",
        "date": f"{datetime.now():%Y-%m-%d %H:%M}",
        "remplacements": remplacements,
        "statut": "a_relire",
    }
    ecrire_note(note, entete, "\n".join(consignes) + "\n\n" + res.texte)
    shutil.move(str(audio), chemin_libre(TRAITES, base, audio.suffix.lower()))
    log(f"→ {note.name} (remplacements : {remplacements}; à vérifier : {len(res.a_verifier)})")
    return note


# --------------------------------------------------------------- rédaction
def rediger(note: Path) -> Path:
    entete, corps = lire_note(note)
    type_note = entete.get("type", "cr").lower()
    if type_note not in {"cr", "rcp"}:
        type_note = "cr"
    # Seconde passe d'anonymisation (filet de sécurité après relecture).
    res = anonymiser(corps, charger_liste_noms(LISTE_NOMS))
    if res.remplacements:
        log(f"Seconde passe : {dict(res.remplacements)} masqué(s) en plus.")
    consigne = (RACINE_PROJET / "prompts" / f"{type_note}.md").read_text(encoding="utf-8")
    entree = f"{consigne}\n\n---\nTRANSCRIPTION ANONYMISÉE :\n\n{res.texte}\n"
    log(f"Rédaction par Claude ({MODELE_CLAUDE})…")
    cmd = [
        os.environ.get("ECOUTE_CLAUDE", "claude"), "-p",
        "--model", MODELE_CLAUDE,
        "--tools", "",
        "--no-session-persistence",
    ]
    proc = subprocess.run(cmd, input=entree, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"Échec de Claude : {proc.stderr.strip() or proc.stdout.strip()}")
    sortie = chemin_libre(PRETS, f"{note.stem}_redige", ".md")
    ecrire_note(sortie, {"type": type_note, "source": note.name, "modele": MODELE_CLAUDE}, proc.stdout)
    if note.parent.resolve() == VALIDE.resolve():
        shutil.move(str(note), chemin_libre(ENVOYES, note.stem, ".md"))
    log(f"→ {sortie}")
    return sortie


# -------------------------------------------------------------- surveillance
def fichiers_stables(dossier: Path, exts: set[str], tailles: dict[Path, int]) -> list[Path]:
    """Fichiers dont la taille n'a pas bougé depuis le passage précédent (copie finie)."""
    prets = []
    for f in sorted(dossier.iterdir()) if dossier.exists() else []:
        if not f.is_file() or f.name.startswith(".") or f.suffix.lower() not in exts:
            continue
        taille = f.stat().st_size
        if taille and tailles.get(f) == taille:
            prets.append(f)
            tailles.pop(f, None)
        else:
            tailles[f] = taille
    return prets


def surveiller(intervalle: float) -> None:
    initialiser()
    log(f"Surveillance de {DOSSIER} (Ctrl+C pour arrêter)")
    tailles: dict[Path, int] = {}
    echecs: set[Path] = set()
    while True:
        for sous in (AUDIO / "CR", AUDIO / "RCP", AUDIO):
            for audio in fichiers_stables(sous, EXTENSIONS_AUDIO, tailles):
                if audio in echecs:
                    continue
                try:
                    traiter_audio(audio)
                except Exception as e:  # noqa: BLE001
                    echecs.add(audio)
                    log(f"ERREUR transcription {audio.name} : {e}")
        for note in fichiers_stables(VALIDE, {".md"}, tailles):
            if note in echecs:
                continue
            try:
                rediger(note)
            except Exception as e:  # noqa: BLE001
                echecs.add(note)
                log(f"ERREUR rédaction {note.name} : {e}")
        time.sleep(intervalle)


def main() -> None:
    p = argparse.ArgumentParser(prog="ecoute", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("surveiller", help="surveille les dossiers et traite automatiquement")
    s.add_argument("--intervalle", type=float, default=5)
    t = sp.add_parser("transcrire", help="transcrit et anonymise un fichier audio")
    t.add_argument("audio", type=Path)
    t.add_argument("--type", choices=["cr", "rcp"])
    r = sp.add_parser("rediger", help="envoie une note relue à Claude")
    r.add_argument("note", type=Path)
    sp.add_parser("dossiers", help="crée les dossiers et les ouvre dans le Finder")
    a = p.parse_args()

    initialiser()
    if a.cmd == "surveiller":
        try:
            surveiller(a.intervalle)
        except KeyboardInterrupt:
            log("Arrêt.")
    elif a.cmd == "transcrire":
        traiter_audio(a.audio.expanduser().resolve(), a.type)
    elif a.cmd == "rediger":
        rediger(a.note.expanduser().resolve())
    elif a.cmd == "dossiers":
        print(DOSSIER)
        subprocess.run(["open", str(DOSSIER)], check=False)


if __name__ == "__main__":
    main()
