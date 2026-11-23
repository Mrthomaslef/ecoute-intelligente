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
import re
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
LISTE_VOCAB = DOSSIER / "vocabulaire.txt"
LISTE_CORRECTIONS = DOSSIER / "corrections.txt"

MODELE_WHISPER = os.environ.get("ECOUTE_WHISPER", "mlx-community/whisper-large-v3-turbo")
MODELE_CLAUDE = os.environ.get("ECOUTE_MODELE", "sonnet")
EXTENSIONS_AUDIO = {".m4a", ".mp3", ".wav", ".aac", ".mp4", ".mov", ".caf", ".ogg", ".flac", ".webm"}
VOCABULAIRE = (
    "Radiologie ostéo-articulaire et interventionnelle, Hôpital Cochin. RCP sarcomes. "
    "IRM, scanner, TEP, biopsie, cryoablation, radiofréquence, cimentoplastie, vissage "
    "percutané, électrochimiothérapie, sclérothérapie, ostéome ostéoïde, chondrosarcome, "
    "tumeur à cellules géantes, kyste anévrismal, métastase, nidus, tassement vertébral, "
    "hypersignal STIR, hyposignal T1, anticoagulants, Kardégic, Xarelto, Eliquis, Plavix, "
    "Préviscan, Lovenox."
)
# Erreurs fréquentes de Whisper sur le vocabulaire médical (complétables via corrections.txt).
CORRECTIONS_DEFAUT = {
    "cardégique": "Kardégic", "cardégic": "Kardégic", "kardégique": "Kardégic",
    "xarelo": "Xarelto", "zarelto": "Xarelto", "éliquis": "Eliquis", "élicuis": "Eliquis",
    "prévisant": "Préviscan", "lovenoxe": "Lovenox", "nidouce": "nidus",
    "flébite": "phlébite", "cimentoplastique": "cimentoplastie", "stire": "STIR",
    "évrum": "névrome", "névrum": "névrome", "interméthatarsien": "intermétatarsien",
    "sous-scannée": "sous scanner", "sous-scanner": "sous scanner",
}


def initialiser() -> None:
    for d in (AUDIO / "CR", AUDIO / "RCP", TRAITES, A_RELIRE, VALIDE, ENVOYES, PRETS):
        d.mkdir(parents=True, exist_ok=True)
    if not LISTE_VOCAB.exists():
        LISTE_VOCAB.write_text(
            "# Termes que Whisper doit bien orthographier (un par ligne) :\n"
            "# noms de molécules, de techniques, de correspondants…\n",
            encoding="utf-8",
        )
    if not LISTE_CORRECTIONS.exists():
        LISTE_CORRECTIONS.write_text(
            "# Corrections automatiques après transcription, une par ligne :\n"
            "# mot mal transcrit = bonne orthographe   (ex. : cardégique = Kardégic)\n",
            encoding="utf-8",
        )
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
HALLUCINATIONS = (
    "sous-titres réalisés par", "sous-titrage", "amara.org", "merci d'avoir regardé",
    "abonnez-vous", "radio-canada", "société radio",
)


def consigne_vocabulaire() -> str:
    """Vocabulaire de base + termes personnels (vocabulaire.txt), tronqué pour Whisper."""
    perso = ""
    if LISTE_VOCAB.exists():
        termes = [l.strip() for l in LISTE_VOCAB.read_text(encoding="utf-8").splitlines()
                  if l.strip() and not l.startswith("#")]
        perso = " " + ", ".join(termes)
    return (VOCABULAIRE + perso)[:800]


def charger_corrections() -> dict[str, str]:
    corr = dict(CORRECTIONS_DEFAUT)
    if LISTE_CORRECTIONS.exists():
        for ligne in LISTE_CORRECTIONS.read_text(encoding="utf-8").splitlines():
            if "=" in ligne and not ligne.lstrip().startswith("#"):
                faux, vrai = ligne.split("=", 1)
                if faux.strip() and vrai.strip():
                    corr[faux.strip().lower()] = vrai.strip()
    return corr


def corriger(texte: str, corrections: dict[str, str] | None = None) -> str:
    for faux, vrai in (corrections or charger_corrections()).items():
        texte = re.sub(rf"(?<!\w){re.escape(faux)}(?!\w)", vrai, texte, flags=re.IGNORECASE)
    return texte


def segment_valide(seg: dict) -> bool:
    texte = seg.get("text", "").strip()
    if not texte:
        return False
    if any(h in texte.lower() for h in HALLUCINATIONS):
        return False
    # Silence probable transcrit à tort.
    return not (seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -1.0)


def transcrire(source, modele: str | None = None, contexte: str = "") -> str:
    """Transcrit un fichier (chemin) ou un tableau numpy 16 kHz, en français."""
    try:
        import mlx_whisper
    except ImportError:
        sys.exit("mlx-whisper absent : lancez ./install.sh")
    res = mlx_whisper.transcribe(
        source if not isinstance(source, Path) else str(source),
        path_or_hf_repo=modele or MODELE_WHISPER,
        language="fr",
        initial_prompt=(consigne_vocabulaire() + " " + contexte[-300:]).strip(),
        condition_on_previous_text=False,
        temperature=(0.0, 0.2, 0.4),
        verbose=None,
    )
    texte = "\n".join(s["text"].strip() for s in res.get("segments", []) if segment_valide(s))
    return corriger(texte)


def transcrire_audio(audio: Path, modele: str | None = None) -> str:
    log(f"Transcription locale ({(modele or MODELE_WHISPER).split('/')[-1]})…")
    return transcrire(audio, modele)


def deviner_type(audio: Path) -> str:
    if audio.parent.name.upper() in {"CR", "RCP"}:
        return audio.parent.name.lower()
    return "rcp" if "rcp" in audio.stem.lower() else "cr"


def traiter_audio(audio: Path, type_note: str | None = None, modele: str | None = None) -> Path:
    type_note = type_note or deviner_type(audio)
    return creer_note(transcrire_audio(audio, modele), type_note, audio)


def creer_note(brut: str, type_note: str, audio: Path | None = None,
               noms_extra: list[str] | None = None) -> Path:
    """Anonymise une transcription et la dépose dans 2_A_Relire (audio rangé dans _traites)."""
    res = anonymiser(brut, charger_liste_noms(LISTE_NOMS) + list(noms_extra or []))
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
    if audio and audio.exists():
        shutil.move(str(audio), chemin_libre(TRAITES, base, audio.suffix.lower()))
    log(f"→ {note.name} (remplacements : {remplacements}; à vérifier : {len(res.a_verifier)})")
    return note


# --------------------------------------------------------------- rédaction
def rediger(note: Path, noms_extra: list[str] | None = None, trame: str | None = None,
            contexte: str | None = None, premiere: bool | None = None) -> Path:
    entete, corps = lire_note(note)
    type_note = entete.get("type", "cr").lower()
    if type_note not in {"cr", "rcp"}:
        type_note = "cr"
    # Seconde passe d'anonymisation (filet de sécurité après relecture).
    res = anonymiser(corps, charger_liste_noms(LISTE_NOMS) + list(noms_extra or []))
    if res.remplacements:
        log(f"Seconde passe : {dict(res.remplacements)} masqué(s) en plus.")
    consigne = (RACINE_PROJET / "prompts" / f"{trame or type_note}.md").read_text(encoding="utf-8")
    entree = f"{consigne}\n\n"
    if contexte:  # dossier antérieur du patient, anonymisé avec les mêmes listes de noms
        ctx = anonymiser(contexte, charger_liste_noms(LISTE_NOMS) + list(noms_extra or []))
        log(f"Dossier antérieur joint ({len(ctx.texte)} caractères, "
            f"{sum(ctx.remplacements.values())} élément(s) masqué(s)).")
        entree += f"---\nDOSSIER ANTÉRIEUR ANONYMISÉ :\n\n{ctx.texte}\n\n"
    if (trame or type_note) == "consultation":
        try:
            from . import modeles
            ex = modeles.exemples(DOSSIER)
            if ex:
                ex = [anonymiser(e, charger_liste_noms(LISTE_NOMS) + list(noms_extra or [])).texte for e in ex]
                log(f"{len(ex)} CR validé(s) joint(s) comme modèles.")
                entree += ("---\nCR VALIDÉS PAR LE DR LEFEVRE (modèles à imiter : style, longueur, concision, "
                           "formulations ; ne reprends AUCUNE donnée de ces patients) :\n\n"
                           + "\n\n=====\n\n".join(ex) + "\n\n")
        except Exception as e:  # noqa: BLE001 — les modèles sont un bonus
            log(f"CR validés non lus ({e}).")
    if premiere is True:
        entree += ("---\nTYPE DE CONSULTATION : PREMIÈRE CONSULTATION (aucun document antérieur "
                   "au nom du patient).\n\n")
    elif premiere is False:
        entree += "---\nTYPE DE CONSULTATION : CONSULTATION AVEC DOSSIER ANTÉRIEUR (suivi possible).\n\n"
    entree += f"---\nTRANSCRIPTION ANONYMISÉE :\n\n{res.texte}\n"
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
    if note.parent.resolve() in {VALIDE.resolve(), A_RELIRE.resolve()}:
        shutil.move(str(note), chemin_libre(ENVOYES, note.stem, ".md"))
    log(f"→ {sortie}")
    return sortie


# ------------------------------------------- transcriptions du jour
TRANSCRIPTIONS = DOSSIER / "Transcriptions"


def ajouter_transcription_du_jour(note: Path, debut: datetime, patient=None) -> Path:
    """Ajoute la transcription anonymisée à ~/EcouteIntelligente/Transcriptions/AAAA-MM-JJ.md (local)."""
    _, corps = lire_note(note)
    texte = "\n".join(l for l in corps.splitlines() if not l.startswith("> ")).strip()
    qui = f"{patient.patient} (créneau {patient.horaire or '?'})" if patient else "patient non identifié"
    TRANSCRIPTIONS.mkdir(parents=True, exist_ok=True)
    fichier = TRANSCRIPTIONS / f"{debut:%Y-%m-%d}.md"
    neuf = not fichier.exists()
    with fichier.open("a", encoding="utf-8") as f:
        if neuf:
            f.write(f"# Transcriptions des consultations du {debut:%d/%m/%Y}\n")
        f.write(f"\n## {debut:%Hh%M} — {qui}\n\n{texte}\n")
    log(f"Transcription ajoutée à {fichier.name}.")
    return fichier


# ------------------------------------------------- consultation → agenda
def notifier(message: str) -> None:
    message = message.replace('"', "'")
    subprocess.run(["osascript", "-e", f'display notification "{message}" with title "Écoute intelligente"'],
                   capture_output=True)


def consultation_vers_agenda(brut: str, audio: Path | None, debut: datetime,
                             fin: datetime | None = None, poste_local: bool = True) -> Path:
    """Tout automatique : anonymisation → Claude → CR déposé dans la fiche patient de l'agenda."""
    from . import agenda

    from . import dossier

    consultations, patient = [], None
    try:
        consultations = agenda.consultations_du(debut)
        patient = agenda.choisir(consultations, debut, brut)
    except Exception as e:  # noqa: BLE001 — l'agenda est un bonus : le CR est produit quand même
        log(f"Agenda inaccessible ({e}).")

    # Dossier antérieur déposé dans Drive (« 1sceenshot et texte com / Consultation JJ/MM ») :
    # uniquement un document AU NOM du patient. Aucun document = première consultation.
    doc, contexte, premiere = None, None, None
    if patient:
        try:
            doc, contexte = dossier.document_du_patient(debut, None, agenda.mots_du_nom(patient.patient))  # document déposé après l'écoute accepté : c'est le nom du patient qui compte
            premiere = doc is None
            log(f"Dossier antérieur trouvé dans Drive : « {doc.titre} »." if doc
                else "Aucun document au nom du patient dans Drive : première consultation.")
        except Exception as e:  # noqa: BLE001 — le CR est produit même sans dossier antérieur
            log(f"Dossier Drive non lu ({e}).")
    else:
        log("Patient non identifié : aucun dossier antérieur joint.")
    titre = doc.titre if doc else ""
    # Les noms des patients du jour (et le titre du document) sont masqués avant tout envoi à Claude.
    noms = sorted({m for c in consultations for m in agenda.mots_du_nom(c.patient)}
                  | set(agenda.mots_du_nom(titre)), key=len, reverse=True)
    note = creer_note(brut, "cr", audio, noms)
    try:
        ajouter_transcription_du_jour(note, debut, patient)
    except Exception as e:  # noqa: BLE001 — bonus : ne bloque jamais le CR
        log(f"Transcription du jour non enregistrée ({e}).")
    sortie = rediger(note, noms, trame="consultation", contexte=contexte, premiere=premiere)
    if doc:
        dossier.marquer_utilise(DOSSIER, debut, doc)
    _, cr = lire_note(sortie)
    ddn, fem = agenda.infos_patient(patient) if patient else (None, None)
    final = agenda.reidentifier(cr, patient, tronquer=False, ddn=ddn, fem=fem) if patient else cr
    pret = chemin_libre(PRETS, f"{sortie.stem}_a_copier", ".txt")
    pret.write_text(final.strip() + "\n", encoding="utf-8")
    if poste_local:  # consultation traitée sur le Mac utilisé : CR prêt à coller
        subprocess.run(["pbcopy"], input=final.strip(), text=True, check=False)
        subprocess.run(["open", "-t", str(pret)], check=False)
    if not patient:
        log("Patient non trouvé dans l'agenda : CR dans 4_CR_Prets.")
        notifier("CR prêt (4_CR_Prets). Patient non trouvé dans l'agenda.")
        if not poste_local:
            subprocess.run(["open", "-t", str(pret)], check=False)
        return sortie
    try:
        agenda.deposer(patient, agenda.reidentifier(cr, patient, ddn=ddn, fem=fem))
    except Exception as e:  # noqa: BLE001
        log(f"Dépôt dans l'agenda impossible ({e}).")
        notifier("CR prêt et copié (⌘V). Dépôt dans l'agenda impossible.")
        return sortie
    log(f"→ CR déposé dans l'agenda : consultation de {patient.horaire or '?'} ({patient.date}).")
    notifier(f"CR prêt et copié (⌘V), déposé dans l'agenda : {patient.patient} ({patient.horaire}).")
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
    from . import relais

    initialiser()
    log(f"Surveillance de {DOSSIER} (Ctrl+C pour arrêter) — mode {relais.mode()}")
    if not relais.est_enregistreur():
        # Mac de traitement : ne doit jamais dormir, sinon les envois du MacBook attendent.
        # (caffeinate -w s'arrête tout seul avec la surveillance ; l'écran peut, lui, s'éteindre.)
        try:
            subprocess.Popen(["/usr/bin/caffeinate", "-ims", "-w", str(os.getpid())],
                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log("Mac de traitement : veille système empêchée tant que la surveillance tourne.")
        except OSError as e:
            log(f"Impossible d'empêcher la veille ({e}).")
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
        # Écoutes interrompues (veille, plantage) : reprises au lieu de rester oubliées dans _en_cours.
        try:
            from .live import EN_COURS, PID
            for wav in relais.ecoutes_interrompues(EN_COURS, PID):
                if wav not in echecs:
                    try:
                        relais.recuperer(wav)
                    except Exception as e:  # noqa: BLE001
                        echecs.add(wav)
                        log(f"ERREUR reprise {wav.name} : {e}")
        except Exception as e:  # noqa: BLE001
            log(f"Contrôle des écoutes interrompues impossible ({e}).")
        # Mac mini : enregistrements envoyés par le MacBook. MacBook (enregistreur) : repli
        # automatique s'ils ne sont pas pris en charge à temps (iCloud bloqué, Mac mini éteint).
        delai = 0 if not relais.est_enregistreur() else relais.DELAI_REPLI
        for wav, info in relais.a_traiter(anciennete_min=delai):
            if wav in echecs:
                continue
            try:
                relais.traiter(wav, info)
            except Exception as e:  # noqa: BLE001
                echecs.add(wav)
                log(f"ERREUR relais {wav.name} : {e}")
        if not relais.est_enregistreur():  # RCP enregistrées sur l'iPhone (Dictaphone → iCloud)
            relais.DEPOT_IPHONE.mkdir(parents=True, exist_ok=True)
            for audio in relais.depots_iphone(tailles):
                if audio in echecs:
                    continue
                try:
                    relais.traiter_iphone(audio)
                except Exception as e:  # noqa: BLE001
                    echecs.add(audio)
                    log(f"ERREUR RCP iPhone {audio.name} : {e}")
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
    c = sp.add_parser("consultation", help="écoute en direct pendant la consultation")
    c.add_argument("--type", choices=["cr", "rcp"], default="cr")
    c.add_argument("--micro", help="numéro ou nom du micro (voir : ecoute micros)")
    c.add_argument("--rapide", action="store_true",
                   help="sans passe finale : garde directement le texte du direct")
    c.add_argument("--precis", action="store_true",
                   help="passe finale avec large-v3 complet (plus lent, plus juste)")
    c.add_argument("--sans-agenda", action="store_true",
                   help="ne pas déposer le CR dans l'Agenda RI (note à relire à la place)")
    c.add_argument("--fichier", type=Path, help=argparse.SUPPRESS)
    c.add_argument("--vitesse", type=float, default=1.0, help=argparse.SUPPRESS)
    rc = sp.add_parser("rcp", help="RCP : son nettoyé, transcription précise, synthèse par dossier")
    rc.add_argument("audios", type=Path, nargs="+", help="un ou plusieurs fichiers (recollés dans l'ordre)")
    su = sp.add_parser("rcp-suite", help="Word, brouillons AP-HP et missions Agenda RI depuis une transcription de RCP")
    su.add_argument("transcription", type=Path, help="fichier texte de la transcription (brute)")
    su.add_argument("--date", required=True, help="date de la RCP (JJ/MM/AAAA)")
    su.add_argument("--sans-brouillons", action="store_true")
    su.add_argument("--sans-agenda", action="store_true")
    su.add_argument("--sans-drive", action="store_true")
    dr = sp.add_parser("drive", help="range le dossier Drive de dépôt (archives) et crée les dossiers du jour")
    dr.add_argument("--essai", action="store_true", help="affiche sans rien déplacer")
    sp.add_parser("micros", help="liste les micros disponibles")
    sp.add_parser("agenda", help="vérifie l'accès à l'Agenda RI et liste les consultations du jour")
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
    elif a.cmd == "consultation":
        from .live import consultation
        micro = int(a.micro) if a.micro and a.micro.isdigit() else a.micro
        note = consultation(
            a.type, micro, a.fichier, a.vitesse, passe_finale=not a.rapide,
            modele_final="mlx-community/whisper-large-v3-mlx" if a.precis else None,
            agenda_auto=not a.sans_agenda,
        )
        if note and a.type == "rcp":
            subprocess.run(["open", "-t", str(note)], check=False)
    elif a.cmd == "rcp":
        from .rcp import rcp_complet
        rcp_complet([f.expanduser().resolve() for f in a.audios])
    elif a.cmd == "rcp-suite":
        from .rcp_suite import suite_rcp
        suite_rcp(a.transcription.expanduser().read_text(encoding="utf-8"),
                  datetime.strptime(a.date, "%d/%m/%Y"), brouillons=not a.sans_brouillons,
                  agenda_ri=not a.sans_agenda, drive=not a.sans_drive)
    elif a.cmd == "drive":
        from .drive_menage import main as menage
        menage(essai=a.essai)
    elif a.cmd == "agenda":
        from . import agenda
        for c in agenda.consultations_du(datetime.now()):
            initiales = "".join(m[0].upper() for m in agenda.mots_du_nom(c.patient))
            print(f"{c.horaire or '?':>8}  {initiales:<5} {c.medecin[:30]:<30} {c.titre[:60]}")
        print("✅ Agenda accessible.")
    elif a.cmd == "micros":
        import sounddevice as sd
        print(sd.query_devices())
    elif a.cmd == "dossiers":
        print(DOSSIER)
        subprocess.run(["open", str(DOSSIER)], check=False)


if __name__ == "__main__":
    main()
