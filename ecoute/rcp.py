"""RCP : traitement après coup de l'enregistrement complet.

En RCP, le MacBook capte de loin plusieurs orateurs : le son est faible (≈ −38 dB) et la
transcription en direct est médiocre. On repart donc du fichier audio :
1. nettoyage et remise à niveau du son (ffmpeg : filtres, débruitage, normalisation dynamique) ;
2. transcription avec le modèle Whisper le plus précis (large-v3 complet) ;
3. anonymisation locale, puis Claude réorganise la transcription dossier par dossier.

Plusieurs fichiers (RCP enregistrée en plusieurs fois) sont recollés dans l'ordre.
Usage : ecoute rcp fichier1.wav [fichier2.wav …]
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from . import cli

MODELE_PRECIS = "mlx-community/whisper-large-v3-mlx"
# Voix lointaines : coupe graves/aigus inutiles, débruite, puis remonte les passages faibles.
FILTRE = ("highpass=f=80,lowpass=f=7500,afftdn=nf=-25,"
          "dynaudnorm=f=250:g=15:p=0.9:m=30,aresample=16000")


def preparer(audios: list[Path], sortie: Path) -> Path:
    """Recolle les fichiers dans l'ordre et améliore le son → WAV 16 kHz mono."""
    ffmpeg = shutil.which("ffmpeg") or str(Path.home() / ".local/bin/ffmpeg")
    entrees = [a for f in audios for a in ("-i", str(f))]
    n = len(audios)
    for filtre in (FILTRE, "aresample=16000"):  # repli : simple recollage si un filtre manque
        graphe = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1,{filtre}[s]"
        proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *entrees,
                               "-filter_complex", graphe, "-map", "[s]", "-ac", "1", str(sortie)],
                              capture_output=True, text=True)
        if proc.returncode == 0:
            return sortie
        cli.log(f"Filtre audio refusé par ffmpeg ({proc.stderr.strip()[-120:]}).")
    raise RuntimeError("préparation audio impossible")


def rcp_complet(audios: list[Path], debut: datetime | None = None, ouvrir: bool = True) -> Path:
    audios = sorted(audios, key=lambda f: f.stat().st_mtime)
    debut = debut or datetime.fromtimestamp(audios[0].stat().st_mtime)
    cli.log(f"RCP : {len(audios)} fichier(s), nettoyage du son…")
    with tempfile.TemporaryDirectory() as tmp:
        propre = preparer(audios, Path(tmp) / "rcp.wav")
        cli.log("RCP : transcription précise (large-v3, quelques minutes)…")
        brut = cli.transcrire_audio(propre, MODELE_PRECIS)
    if not brut.strip():
        raise RuntimeError("aucune parole reconnue dans l'enregistrement")
    note = cli.creer_note(brut, "rcp")
    sortie = cli.rediger(note, trame="rcp")
    for a in audios:  # audio rangé une fois la synthèse produite
        if a.exists() and a.parent.resolve() != cli.TRAITES.resolve():
            shutil.move(str(a), cli.chemin_libre(cli.TRAITES, f"{debut:%Y-%m-%d_%Hh%M}_rcp", a.suffix))
    cli.log(f"→ Synthèse de RCP : {sortie}")
    cli.notifier("Synthèse de la RCP prête (dossier par dossier).")
    # Suite de RCP (Mac de traitement) : Word nominatif, brouillons AP-HP, missions dans l'Agenda RI.
    from . import relais
    if not relais.est_enregistreur() and os.environ.get("ECOUTE_SUITE_RCP", "1") != "0":
        try:
            from .rcp_suite import suite_rcp
            suite_rcp(brut, debut)
        except Exception as e:  # noqa: BLE001 — la synthèse .md reste disponible
            cli.log(f"Suite de RCP impossible ({e}).")
    if ouvrir:
        subprocess.run(["open", "-t", str(sortie)], check=False)
    return sortie
