"""Mode consultation : écoute du micro, transcription locale en direct, passe finale soignée.

Pendant la consultation : chaque phrase s'affiche quelques secondes après avoir été prononcée.
À la fin (Ctrl+C) : l'enregistrement complet est retranscrit en une fois, avec tout le
contexte (plus juste que le direct), puis anonymisé et déposé dans 2_A_Relire.
Tout reste sur le Mac ; l'audio est sauvegardé en continu (rien n'est perdu en cas de coupure).
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

from . import cli

TAUX = 16_000
TRAME = 480  # 30 ms
EN_COURS = cli.AUDIO / "_en_cours"
PID = cli.DOSSIER / ".consultation.pid"  # lu par bin/ecoute-bouton (marche/arrêt)


class Segmenteur:
    """Découpe le flux audio en phrases grâce aux silences (détection d'activité vocale)."""

    def __init__(self, silence_fin: float = 0.7, duree_max: float = 20.0, preroll: float = 0.3):
        self.trames_silence_fin = int(silence_fin * TAUX / TRAME)
        self.trames_max = int(duree_max * TAUX / TRAME)
        self.trames_preroll = int(preroll * TAUX / TRAME)
        self.bruit = 0.003
        self.avant: list[np.ndarray] = []
        self.courant: list[np.ndarray] = []
        self.silences = 0
        self.voix_consecutives = 0

    def seuil(self) -> float:
        return max(0.006, self.bruit * 3.0)

    def ajouter(self, trame: np.ndarray) -> np.ndarray | None:
        rms = float(np.sqrt(np.mean(trame ** 2)))
        voix = rms > self.seuil()
        if not self.courant:
            if not voix:  # adapte le niveau de bruit de fond de la salle
                self.bruit = 0.98 * self.bruit + 0.02 * rms
                self.voix_consecutives = 0
            else:
                self.voix_consecutives += 1
            self.avant = (self.avant + [trame])[-self.trames_preroll:]
            if self.voix_consecutives >= 3:  # 90 ms de voix : début de phrase
                self.courant, self.avant, self.silences = list(self.avant), [], 0
            return None
        self.courant.append(trame)
        self.silences = 0 if voix else self.silences + 1
        if self.silences >= self.trames_silence_fin or len(self.courant) >= self.trames_max:
            return self.vider()
        return None

    def vider(self) -> np.ndarray | None:
        seg, self.courant, self.voix_consecutives = self.courant, [], 0
        return np.concatenate(seg) if len(seg) > 10 else None


def source_micro(file_trames: queue.Queue, arret: threading.Event, appareil=None) -> None:
    import sounddevice as sd

    def rappel(indata, _frames, _t, statut):
        if statut:
            print(f"  (micro : {statut})", file=sys.stderr)
        file_trames.put(indata[:, 0].copy())

    try:
        with sd.InputStream(samplerate=TAUX, channels=1, dtype="float32", blocksize=TRAME,
                            callback=rappel, device=appareil):
            arret.wait()
    except Exception as e:  # noqa: BLE001 — pas de micro, accès refusé…
        print(f"\n❌ Micro inaccessible ({e}).\n   Branchez un micro ou autorisez le Terminal dans "
              "Réglages système › Confidentialité et sécurité › Micro.", file=sys.stderr, flush=True)
        arret.set()


def source_fichier(chemin: Path, file_trames: queue.Queue, arret: threading.Event,
                   vitesse: float) -> None:
    """Simule le micro à partir d'un fichier (tests, ou consultation enregistrée ailleurs)."""
    from mlx_whisper.audio import load_audio

    audio = np.asarray(load_audio(str(chemin)), dtype=np.float32)
    for i in range(0, len(audio), TRAME):
        if arret.is_set():
            break
        trame = audio[i:i + TRAME]
        if len(trame) < TRAME:
            trame = np.pad(trame, (0, TRAME - len(trame)))
        file_trames.put(trame)
        if vitesse:
            time.sleep(TRAME / TAUX / vitesse)
    file_trames.put(np.zeros(int(TAUX * 1.5), dtype=np.float32))  # silence final
    arret.set()


def consultation(type_note: str = "cr", appareil=None, fichier: Path | None = None,
                 vitesse: float = 1.0, passe_finale: bool = True,
                 modele_final: str | None = None, agenda_auto: bool = True) -> Path | None:
    cli.initialiser()
    EN_COURS.mkdir(parents=True, exist_ok=True)
    PID.write_text(str(os.getpid()))
    try:
        return _consultation(type_note, appareil, fichier, vitesse, passe_finale, modele_final,
                             agenda_auto)
    finally:
        PID.unlink(missing_ok=True)


def _consultation(type_note, appareil, fichier, vitesse, passe_finale, modele_final,
                  agenda_auto) -> Path | None:
    debut = datetime.now()
    wav_chemin = cli.chemin_libre(EN_COURS, f"{debut:%Y-%m-%d_%Hh%M}_{type_note}", ".wav")
    wav = wave.open(str(wav_chemin), "wb")
    wav.setnchannels(1), wav.setsampwidth(2), wav.setframerate(TAUX)

    file_trames: queue.Queue = queue.Queue()
    file_segments: queue.Queue = queue.Queue()
    arret = threading.Event()
    textes: list[str] = []

    def transcripteur():
        while (item := file_segments.get()) is not None:
            t0, seg = item
            texte = cli.transcrire(seg, contexte=" ".join(textes))
            if texte:
                textes.append(texte)
                m, s = divmod(int(t0), 60)
                print(f"\033[2m[{m:02d}:{s:02d}]\033[0m {texte}", flush=True)

    # Charge le modèle avant de commencer (la première phrase ne doit pas attendre).
    cli.log("Chargement du modèle Whisper…")
    cli.transcrire(np.zeros(TAUX, dtype=np.float32))
    ouvrier = threading.Thread(target=transcripteur, daemon=True)
    ouvrier.start()
    capteur = threading.Thread(
        target=source_fichier if fichier else source_micro,
        args=(fichier, file_trames, arret, vitesse) if fichier else (file_trames, arret, appareil),
        daemon=True,
    )
    capteur.start()
    print("\n\033[1m🎙  Écoute en cours — parlez normalement. Ctrl+C pour terminer.\033[0m\n",
          flush=True)

    seg = Segmenteur()
    echantillons = 0
    try:
        while not (arret.is_set() and file_trames.empty()):
            try:
                trame = file_trames.get(timeout=0.2)
            except queue.Empty:
                continue
            wav.writeframes((np.clip(trame, -1, 1) * 32767).astype(np.int16).tobytes())
            for i in range(0, len(trame), TRAME):
                phrase = seg.ajouter(trame[i:i + TRAME])
                if phrase is not None:
                    file_segments.put(((echantillons + i) / TAUX - len(phrase) / TAUX, phrase))
            echantillons += len(trame)
    except KeyboardInterrupt:
        print()
    arret.set()
    capteur.join(timeout=2)
    if (reste := seg.vider()) is not None:
        file_segments.put((echantillons / TAUX, reste))
    file_segments.put(None)
    cli.log("Fin de l'écoute, dernières phrases…")
    ouvrier.join()
    wav.close()
    duree = echantillons / TAUX
    cli.log(f"Enregistrement : {int(duree // 60)} min {int(duree % 60)} s")

    if duree < 1 and not textes:  # rien enregistré (micro absent…) : pas de note vide
        wav_chemin.unlink(missing_ok=True)
        return None
    brut = "\n".join(textes)
    if passe_finale and duree > 2:
        cli.log("Passe finale sur l'enregistrement complet (plus précise)…")
        try:
            brut = cli.transcrire_audio(wav_chemin, modele_final)
        except Exception as e:  # noqa: BLE001 — on garde au moins le texte du direct
            cli.log(f"Passe finale impossible ({e}) : on garde la transcription du direct.")
    if type_note == "cr" and agenda_auto:
        return cli.consultation_vers_agenda(brut, wav_chemin, debut)
    return cli.creer_note(brut, type_note, wav_chemin)
