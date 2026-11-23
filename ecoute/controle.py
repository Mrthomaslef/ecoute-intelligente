"""Contrôle automatique du lundi matin (Mac mini « traitement »).

`ecoute controle` vérifie toute la chaîne, RÉPARE seul ce qui peut l'être (service relancé,
mise à jour du code, dossiers du jour…), revérifie, puis envoie un iMessage court à Thomas :
✅ si tout est prêt, sinon la liste de ce qui ne va pas et ce qu'il faut faire.

Ne lit ni n'envoie aucune donnée patient : l'agenda n'est compté (nombre de consultations).
Options : --sans-message (affiche seulement), --sans-rangement (ne touche pas au Drive).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import dossier, relais

REPO = Path(__file__).resolve().parent.parent
SERVICE = "com.thomaslefevre.ecoute"
MOI = os.environ.get("ECOUTE_IMESSAGE", "+33658552152")
MAIL_APHP = "thomas-jean.lefevre@aphp.fr"
MODELES = ("whisper-large-v3-turbo", "whisper-large-v3-mlx")
EXT_AUDIO = {".m4a", ".mp3", ".wav", ".aac", ".mp4", ".mov", ".caf", ".qta"}


def _run(cmd: list[str], timeout: int = 120, **kw) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


# ------------------------------------------------------------------ vérifications
# Chaque vérification renvoie (ok, détail). Les réparations éventuelles sont faites à part.

def v_code() -> tuple[bool, str]:
    _run(["git", "-C", str(REPO), "fetch", "-q"], 60)
    etat = _run(["git", "-C", str(REPO), "status", "-sb"]).stdout.splitlines()
    tete = etat[0] if etat else ""
    if "behind" in tete:
        return False, "code en retard sur GitHub"
    if len(etat) > 1:
        return True, "code à jour (modifications locales non poussées)"
    return True, "code à jour"


def r_code() -> None:
    _run(["git", "-C", str(REPO), "pull", "--ff-only", "-q"], 120)


def v_tests() -> tuple[bool, str]:
    p = _run([str(REPO / ".venv" / "bin" / "python"), "-m", "unittest", "discover", "-s", "tests"],
             300, cwd=REPO)
    sortie = (p.stdout + p.stderr).strip().splitlines()
    n = next((l.split()[1] for l in sortie if l.startswith("Ran ")), "?")
    der = f"{n} réussis" if p.returncode == 0 else (sortie[-1] if sortie else "?")
    return p.returncode == 0, f"tests : {der}"


def v_service() -> tuple[bool, str]:
    p = _run(["launchctl", "list", SERVICE])
    if p.returncode != 0 or '"PID"' not in p.stdout:
        return False, "service de traitement arrêté"
    return True, "service de traitement actif"


def v_veille() -> tuple[bool, str]:
    p = _run(["pmset", "-g", "assertions"])
    ok = "caffeinate" in p.stdout
    return ok, "veille du Mac mini empêchée" if ok else "le Mac mini peut s'endormir"


def r_service() -> None:
    _run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{SERVICE}"])
    time.sleep(15)


def v_icloud() -> tuple[bool, str]:
    manquants = [d.name for d in (relais.RELAIS, relais.DEPOT_IPHONE) if not d.is_dir()]
    if manquants:
        return False, f"dossier iCloud absent : {', '.join(manquants)}"
    vieux = [f for d in (relais.RELAIS, relais.DEPOT_IPHONE) for f in d.iterdir()
             if f.is_file() and f.suffix.lower() in EXT_AUDIO and time.time() - f.stat().st_mtime > 3 * 3600]
    if vieux:
        return False, f"{len(vieux)} enregistrement(s) en souffrance dans iCloud"
    return True, "relais iCloud vide et prêt"


def r_icloud() -> None:
    for d in (relais.RELAIS, relais.DEPOT_IPHONE):
        d.mkdir(parents=True, exist_ok=True)
        for f in d.iterdir():
            if f.is_file() and f.suffix.lower() in EXT_AUDIO:
                relais.present_localement(f)  # lance le téléchargement iCloud si besoin
    r_service()


def v_modeles() -> tuple[bool, str]:
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    absents = [m for m in MODELES if not (hub / f"models--mlx-community--{m}").is_dir()]
    libre = shutil.disk_usage("/").free / 1e9
    if absents:
        return False, f"modèle de transcription absent : {', '.join(absents)}"
    if libre < 15:
        return False, f"disque presque plein ({libre:.0f} Go libres)"
    return True, f"modèles de transcription présents, {libre:.0f} Go libres"


def v_claude() -> tuple[bool, str]:
    p = _run(["claude", "-p", "--model", "sonnet", "--tools", "", "--no-session-persistence"],
             180, input="Réponds seulement OK")
    ok = p.returncode == 0 and "OK" in p.stdout.upper()
    return ok, "Claude répond" if ok else "Claude ne répond pas (connexion / abonnement ?)"


def v_agenda() -> tuple[bool, str]:
    try:
        from . import agenda
        n = len(agenda.consultations_du(datetime.now()))
        return True, f"Agenda RI accessible ({n} consultation(s) aujourd'hui)"
    except Exception as e:  # noqa: BLE001
        return False, f"Agenda RI inaccessible ({type(e).__name__})"


def v_mail() -> tuple[bool, str]:
    p = _run(["osascript", "-e", 'tell application "Mail" to get email addresses of every account'], 60)
    ok = MAIL_APHP in p.stdout
    return ok, "Mail voit le compte AP-HP" if ok else "Mail ne voit pas le compte AP-HP (brouillons RCP impossibles)"


def v_drive() -> tuple[bool, str]:
    j = datetime.now()
    p = _run([dossier.rclone(), "lsf", "--dirs-only", dossier.RACINE], 120)
    if p.returncode != 0:
        return False, "Drive inaccessible (rclone)"
    noms = {n.rstrip("/") for n in p.stdout.splitlines()}
    attendus = [f"Consultation {j:%d}／{j:%m}", f"RCP {j:%d}／{j:%m}"]
    manque = [a for a in attendus if a not in noms]
    if manque:
        return False, f"dossier(s) du jour absent(s) : {', '.join(m.replace('／', '/') for m in manque)}"
    return True, f"Drive OK, dossiers du jour présents ({j:%d/%m})"


def r_drive() -> None:
    from .drive_menage import menage
    menage()  # ne fait que créer les dossiers manquants si rien n'est à ranger


VERIFS = [  # (nom, vérification, réparation ou None, consigne si l'échec persiste)
    ("Code", v_code, r_code, "faire git pull dans le dépôt ecoute-intelligente"),
    ("Tests", v_tests, None, "me prévenir : un test échoue"),
    ("Service", v_service, r_service, "redémarrer le Mac mini"),
    ("Veille", v_veille, r_service, "Réglages › Économie d'énergie : empêcher la suspension"),
    ("iCloud", v_icloud, r_icloud, "ouvrir le Finder › iCloud Drive › EcouteIntelligente sur le Mac mini"),
    ("Modèles", v_modeles, None, "libérer de la place / me prévenir"),
    ("Claude", v_claude, None, "lancer `claude` dans le Terminal du Mac mini et se reconnecter"),
    ("Agenda", v_agenda, None, "vérifier la connexion Internet du Mac mini"),
    ("Mail", v_mail, None, "ouvrir Mail sur le Mac mini et vérifier le compte AP-HP"),
    ("Drive", v_drive, r_drive, "créer à la main « Consultation JJ/MM » et « RCP JJ/MM »"),
]


def controler(rangement: bool = True) -> tuple[bool, list[str], list[str]]:
    lignes, rangees = [], []
    if rangement:
        try:
            from .drive_menage import menage
            rangees = menage()
        except Exception as e:  # noqa: BLE001
            rangees = [f"⚠️ rangement Drive impossible : {e}"]
    tout_ok = True
    for nom, verif, reparer, consigne in VERIFS:
        try:
            ok, detail = verif()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"
        repare = False
        if not ok and reparer:
            try:
                reparer()
                ok, detail = verif()
                repare = ok
            except Exception as e:  # noqa: BLE001
                detail += f" (réparation impossible : {e})"
        tout_ok &= ok
        lignes.append(("✅ " if ok else "❌ ") + detail + (" — réparé automatiquement" if repare else "")
                      + ("" if ok else f" → {consigne}"))
    return tout_ok, lignes, rangees


def message(tout_ok: bool, lignes: list[str], rangees: list[str]) -> str:
    j = datetime.now()
    tete = (f"🎙️ Écoute intelligente — lundi {j:%d/%m} : tout est prêt ✅" if tout_ok
            else f"🎙️ Écoute intelligente — lundi {j:%d/%m} : ⚠️ À VÉRIFIER avant la consultation")
    deplaces = [r for r in rangees if r.startswith("→")]
    autres = [r.replace("／", "/") for r in rangees if not r.startswith("→")]
    corps = [tete, ""]
    if deplaces:
        corps.append(f"🗂️ Drive rangé : {len(deplaces)} élément(s) archivé(s)")
    corps += autres
    corps += lignes if not tout_ok else [l for l in lignes if "réparé" in l] or ["Service, Mac éveillé, iCloud, Claude, Agenda, Mail AP-HP, Drive : OK."]
    corps += ["", "📂 Déposer : document antérieur de chaque patient dans « Consultation "
              f"{j:%d/%m} », liste « NOM Prénom IPP » + CR dans « RCP {j:%d/%m} ».",
              "🧪 Test éventuel : 🟢 Écoute RCP uniquement (jamais 🟣 Consultation)."]
    return "\n".join(corps)


def envoyer_imessage(texte: str) -> bool:
    script = ('on run argv\n tell application "Messages" to send (item 1 of argv) to participant '
              '(item 2 of argv) of (1st account whose service type = iMessage)\nend run')
    p = _run(["osascript", "-e", script, texte, MOI], 60)
    if p.returncode != 0:  # repli : notification locale
        _run(["osascript", "-e", f'display notification "{texte[:200]}" with title "Écoute intelligente"'])
    return p.returncode == 0


def main(envoyer: bool = True, rangement: bool = True) -> int:
    tout_ok, lignes, rangees = controler(rangement)
    texte = message(tout_ok, lignes, rangees)
    print(texte)
    print("\n--- détail ---\n" + "\n".join(lignes))
    (Path.home() / "EcouteIntelligente" / ".controle_lundi.txt").write_text(
        f"{datetime.now():%Y-%m-%d %H:%M}\n{texte}\n\n" + "\n".join(lignes) + "\n", encoding="utf-8")
    if envoyer:
        print("iMessage envoyé." if envoyer_imessage(texte) else "iMessage impossible : notification locale.")
    return 0 if tout_ok else 1
