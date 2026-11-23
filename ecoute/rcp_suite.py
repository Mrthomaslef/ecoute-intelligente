"""Après la RCP : Word nominatif, brouillons de mails AP-HP et missions dans l'Agenda RI.

1. La liste préparatoire de la RCP est lue dans Google Drive
   (« 1sceenshot et texte com / RCP JJ/MM ») : noms, IPP, dates de naissance, CR d'imagerie.
2. Chaque patient reçoit un repère ⟦P1⟧, ⟦P2⟧… ; la liste et la transcription sont anonymisées
   LOCALEMENT (noms, IPP, dates remplacés) avant d'être envoyées à Claude.
3. Claude rend un JSON : dossiers, missions de l'équipe de RI, brouillons de mails ({PATIENT}).
4. Sur le Mac, les identités sont réinsérées : Word « Synthèse RCP JJ-MM-AAAA.docx » (Drive +
   4_CR_Prets), brouillons dans la boîte AP-HP (app Mail, jamais envoyés), missions dans
   l'encart « Questions perso » de l'Agenda RI (filtre RCP).
Chaque étape est indépendante : si l'une échoue, les autres sont faites quand même.
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import cli
from .anonymiser import anonymiser, charger_liste_noms

MOI = "thomas-jean.lefevre@aphp.fr"
SIGNATURE = (
    "______________________________________________\n"
    "Dr Thomas LEFEVRE\nAssistant spécialiste\nImagerie ostéo-articulaire\n"
    "Service de Radiologie B\nHôpital Cochin\nAP-HP. Centre - Université Paris Cité\n"
    "27 rue du Faubourg Saint-Jacques\n75014 PARIS\nTél: 33.(0)1.58.41.24.89"
)
SECRETARIAT = "rdv.radiob.cch@aphp.fr"
SECRETAIRES_CS = ["mariana.gameirojorge@aphp.fr", "gysleve.mouyoungui@aphp.fr"]
EQUIPE_RI = [
    "fadila.mihoubibouvier@aphp.fr", "amandine.laporte@aphp.fr", "stefano.lusi@aphp.fr",
    "macil.hamadouche@aphp.fr", "marie.talabard@aphp.fr", "yazid.allel@aphp.fr",
    "hippolyte.monnier@aphp.fr",
]
# Règles d'adressage observées dans les mails envoyés (modifiables : destinataires_rcp.json).
DESTINATAIRES = {
    "programmation": {"a": [SECRETARIAT], "cc": EQUIPE_RI},
    "consultation": {"a": [SECRETARIAT, *SECRETAIRES_CS], "cc": EQUIPE_RI},
    "correspondant": {"a": [], "cc": ["marie.talabard@aphp.fr"]},
}
INCONNU = "[PATIENT À COMPLÉTER]"


def destinataires() -> dict:
    f = cli.DOSSIER / "destinataires_rcp.json"
    if f.exists():
        try:
            return {**DESTINATAIRES, **json.loads(f.read_text(encoding="utf-8"))}
        except ValueError:
            cli.log("destinataires_rcp.json illisible : règles par défaut.")
    return DESTINATAIRES


# ------------------------------------------------------------- patients
@dataclass
class PatientRCP:
    nom: str                # NOM (majuscules)
    prenom: str = ""
    ipp: str = ""
    naissance: str = ""     # JJ/MM/AAAA
    sexe: str = ""          # "F" / "M" / ""
    autres_noms: tuple[str, ...] = ()  # prénoms secondaires, nom de naissance
    label: str = ""

    @property
    def civilite(self) -> str:
        return {"F": "Mme", "M": "M."}.get(self.sexe, "")

    def identite(self) -> str:
        """« Mme NOM Prénom (née le JJ/MM/AAAA) — IPP 80… » (format des mails du service)."""
        s = " ".join(x for x in (self.civilite, self.nom, self.prenom) if x)
        if self.naissance:
            s += f" ({'née' if self.sexe == 'F' else 'né'} le {self.naissance})"
        if self.ipp:
            s += f" — IPP {self.ipp}"
        return s

    def age(self, jour: datetime) -> str:
        try:
            n = datetime.strptime(self.naissance, "%d/%m/%Y")
        except ValueError:
            return ""
        return str(jour.year - n.year - ((jour.month, jour.day) < (n.month, n.day)))

    def mots(self) -> list[str]:
        mots = re.split(r"[\s\-]+", self.nom) + [self.prenom] + list(self.autres_noms)
        return [m for m in dict.fromkeys(mots) if len(m) >= 3]


CIV = r"(?:Mme|Madame|Mlle|M\.|Mr\.?|Monsieur)"
NOM_MAJ = r"[A-ZÀ-ÖØ-Ý][A-ZÀ-ÖØ-Ý'’\-]+(?:\s+[A-ZÀ-ÖØ-Ý][A-ZÀ-ÖØ-Ý'’\-]+)*"
PRENOM = r"[A-ZÀ-ÖØ-Ý][a-zà-öø-ÿ'’]+(?:[\- ][A-ZÀ-ÖØ-Ý][a-zà-öø-ÿ'’]+)?"
LIGNE_PATIENT = re.compile(rf"^\s*(?P<civ>{CIV})?\s*(?P<nom>{NOM_MAJ})\s+(?P<prenom>{PRENOM})(?P<reste>.*)$")
IPP = re.compile(r"(?<!\d)(8\d{9})(?!\d)")
NAISSANCE = re.compile(r"n[ée]e?\s+le\s+(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
BLOC = re.compile(r"Nom,\s*Pr[ée]nom\s*:\s*(?P<nom>[^,\n]+),\s*(?P<prenoms>[^\n(]+)(?:\((?P<civ>[^)]*)\))?")


def _sexe(civ: str) -> str:
    c = (civ or "").lower().rstrip(".")
    return "F" if c in {"mme", "madame", "mlle"} else "M" if c in {"m", "mr", "monsieur"} else ""


def lire_patients(texte: str) -> list[PatientRCP]:
    """Liste en tête du document (« NOM Prénom IPP ») complétée par les en-têtes de CR."""
    patients: list[PatientRCP] = []
    entete = re.split(r"\n\s*Nom,\s*Pr[ée]nom\s*:", texte, maxsplit=1)[0]
    for ligne in entete.splitlines():
        m = LIGNE_PATIENT.match(ligne)
        if not m or not (IPP.search(ligne) or NAISSANCE.search(ligne)):
            continue
        reste = m.group("reste")
        sexe = _sexe(m.group("civ") or "") or ("F" if re.search(r"\bnée\b", reste) else "")
        ipp, nais = IPP.search(ligne), NAISSANCE.search(ligne)
        patients.append(PatientRCP(m.group("nom").strip(), m.group("prenom").strip(),
                                   ipp.group(1) if ipp else "", nais.group(1) if nais else "", sexe))
    # En-têtes de CR : date de naissance, sexe, prénoms secondaires, nom de naissance.
    for b in BLOC.finditer(texte):
        suite = texte[b.end():b.end() + 600]
        nom = b.group("nom").strip().upper()
        prenoms = b.group("prenoms").split()
        nais = re.search(r"Date de naissance\s*:\s*(\d{2}/\d{2}/\d{4})", suite)
        num = re.search(r"N° patient, sexe\s*:\s*(\d+),\s*(\w+)", suite)
        naiss_nom = re.search(r"Nom de naissance\s*:\s*([A-ZÀ-Ý'’\- ]{3,})\n", suite)
        sexe = _sexe(b.group("civ") or "") or (
            {"féminin": "F", "masculin": "M"}.get(num.group(2).lower(), "") if num else "")
        p = next((p for p in patients if (num and p.ipp == num.group(1)) or p.nom == nom), None)
        if p is None:
            p = PatientRCP(nom, prenoms[0] if prenoms else "")
            patients.append(p)
        p.ipp = p.ipp or (num.group(1) if num else "")
        p.naissance = p.naissance or (nais.group(1) if nais else "")
        p.sexe = p.sexe or sexe
        autres = list(prenoms[1:]) + ([naiss_nom.group(1).strip()] if naiss_nom else [])
        p.autres_noms = tuple(dict.fromkeys([*p.autres_noms, *(a for a in autres if a.upper() != p.nom)]))
    for i, p in enumerate(patients, 1):
        p.label = f"P{i}"
    return patients


# -------------------------------------------------------- anonymisation
def _souple(mot: str) -> str:
    """Motif insensible aux accents et à la casse (Whisper écrit « Roussel » ou « Rousselle »)."""
    variantes = {"a": "aàâä", "e": "eéèêë", "i": "iîï", "o": "oôö", "u": "uùûü", "c": "cç", "y": "yÿ"}
    base = "".join(c for c in unicodedata.normalize("NFKD", mot.lower()) if unicodedata.category(c) != "Mn")
    return "".join(f"[{variantes[c]}]" if c in variantes else re.escape(c) for c in base)


def etiqueter(texte: str, patients: list[PatientRCP]) -> str:
    """Remplace IPP et noms de chaque patient par son repère ⟦Pn⟧ (sauf « Dr / Pr NOM »)."""
    for p in patients:
        if p.ipp:
            texte = texte.replace(p.ipp, f"⟦{p.label}⟧")
        for mot in sorted(p.mots(), key=len, reverse=True):
            motif = re.compile(rf"(?<!\w){_souple(mot)}(?!\w)", re.IGNORECASE)

            def remplacer(m: re.Match, lab=p.label) -> str:
                avant = texte_courant[max(0, m.start() - 12):m.start()]
                return m.group(0) if re.search(r"\b(Dr|Pr|Docteur|Professeur)\.?\s*$", avant) else f"⟦{lab}⟧"
            texte_courant = texte
            texte = motif.sub(remplacer, texte)
        texte = re.sub(rf"(?:{CIV}\s*)?⟦{p.label}⟧(?:[\s,]*⟦{p.label}⟧)*", f"⟦{p.label}⟧", texte)
    return texte


def preparer_liste(texte: str, patients: list[PatientRCP], jour: datetime) -> str:
    """Liste préparatoire sans lignes d'identité, chaque patient résumé par repère, sexe et âge."""
    lignes = []
    for ligne in texte.splitlines():
        if re.match(r"\s*(Nom de naissance|Date de naissance|N° patient|N° examen)\b", ligne):
            continue
        lignes.append(ligne)
    resume = "\n".join(
        f"⟦{p.label}⟧ : {({'F': 'femme', 'M': 'homme'}.get(p.sexe, 'sexe ?'))}"
        f"{', ' + p.age(jour) + ' ans' if p.age(jour) else ''}" for p in patients)
    return f"PATIENTS INSCRITS :\n{resume}\n\nDOCUMENTS :\n" + etiqueter("\n".join(lignes), patients)


def anonymiser_pour_claude(texte: str, patients: list[PatientRCP]) -> str:
    noms = charger_liste_noms(cli.LISTE_NOMS) + [m for p in patients for m in p.mots()]
    return anonymiser(etiqueter(texte, patients), noms).texte


# ------------------------------------------------------------ Drive
def est_dossier_rcp(nom: str, jour: datetime) -> bool:
    from .dossier import _norm
    n = _norm(nom)
    return "rcp" in n and re.search(rf"(?<!\d)0?{jour.day}\D{{1,3}}0?{jour.month}(?!\d)", n) is not None


def liste_preparatoire(jour: datetime) -> tuple[str, str | None]:
    """(texte des documents du dossier « RCP JJ/MM », nom du dossier Drive) ; ('', None) si absent."""
    from . import dossier
    for d in dossier._lsjson(""):
        if d.get("IsDir") and est_dossier_rcp(d["Name"], jour):
            textes = []
            for f in dossier._lsjson(d["Name"], "-R", "--files-only"):
                if Path(f["Name"]).suffix.lower() not in dossier.EXTENSIONS_TEXTE | {".pdf"}:
                    continue
                if dossier._norm(f["Name"]).startswith("synthese"):
                    continue  # nos propres synthèses
                doc = dossier.Document(f.get("ID") or f["Path"], f"{d['Name']}/{f['Path']}",
                                       f["Name"], dossier._date(f["ModTime"]))
                textes.append(dossier.lire(doc, limite=60000))
            return "\n\n".join(textes), d["Name"]
    return "", None


# ------------------------------------------------------------ Claude
def demander_claude(liste: str, transcription: str) -> dict:
    consigne = (cli.RACINE_PROJET / "prompts" / "rcp_suite.md").read_text(encoding="utf-8")
    entree = (f"{consigne}\n\n---\nLISTE PRÉPARATOIRE ANONYMISÉE :\n\n{liste or '(aucune liste déposée)'}"
              f"\n\n---\nTRANSCRIPTION ANONYMISÉE :\n\n{transcription}\n")
    cmd = [os.environ.get("ECOUTE_CLAUDE", "claude"), "-p", "--model", cli.MODELE_CLAUDE,
           "--tools", "", "--no-session-persistence"]
    proc = subprocess.run(cmd, input=entree, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"Claude : {proc.stderr.strip() or proc.stdout.strip()}")
    return lire_json(proc.stdout)


def lire_json(sortie: str) -> dict:
    s = sortie.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    debut, fin = s.find("{"), s.rfind("}")
    data = json.loads(s[debut:fin + 1])
    for k in ("dossiers", "missions", "mails"):
        data.setdefault(k, [])
    return data


# ---------------------------------------------------- réidentification
def _patient(data_item: dict, patients: list[PatientRCP]) -> PatientRCP | None:
    lab = str(data_item.get("patient") or "").strip("⟦⟧ ")
    return next((p for p in patients if p.label == lab), None)


def reidentifier(texte: str, patients: list[PatientRCP]) -> str:
    for p in patients:
        nom = " ".join(x for x in (p.civilite, p.nom, p.prenom) if x)
        texte = re.sub(rf"⟦{p.label}⟧|(?<![\w⟦]){p.label}(?![\w⟧])", nom, texte)
    return texte


def completer_sexe(data: dict, patients: list[PatientRCP]) -> None:
    """Sexe absent de la liste : repris de l'âge/sexe déduit de la séance (« Femme, 96 ans »)."""
    for d in data.get("dossiers", []):
        p = _patient(d, patients)
        if p and not p.sexe:
            a = str(d.get("age_sexe", "")).lower()
            p.sexe = "F" if a.startswith("femme") else "M" if a.startswith("homme") else ""


def mails_finaux(data: dict, patients: list[PatientRCP], jour: datetime) -> list[dict]:
    regles, mails = destinataires(), []
    for m in data.get("mails", []):
        p = _patient(m, patients)
        type_ = m.get("type") if m.get("type") in regles else "correspondant"
        ident = p.identite() if p else INCONNU
        corps = reidentifier(str(m.get("corps", "")).replace("{PATIENT}", ident), patients).strip()
        if type_ in {"programmation", "consultation"} or not m.get("objet"):
            objet = ident if p else f"RCP du {jour:%d/%m} — {INCONNU}"
        else:
            objet = reidentifier(str(m["objet"]), patients)
            if p:
                objet += f" — {p.nom} {p.prenom}".rstrip()
        mails.append({"type": type_, "objet": objet, "corps": f"{corps}\n\n{SIGNATURE}",
                      "a": list(regles[type_].get("a", [])), "cc": list(regles[type_].get("cc", [])),
                      "patient": p.identite() if p else ""})
    return mails


# ------------------------------------------------------------ Word
def ecrire_word(data: dict, patients: list[PatientRCP], jour: datetime, sortie: Path) -> Path:
    e = lambda s: html.escape(reidentifier(str(s or ""), patients))  # noqa: E731
    morceaux = [
        f"<h1>Synthèse de la RCP du {jour:%d/%m/%Y}</h1>",
        f"<p><i>Synthèse établie automatiquement à partir de l'enregistrement de séance et de la "
        f"liste préparatoire. Décisions à vérifier avec la fiche RCP officielle.</i></p>",
    ]
    if data.get("limites"):
        morceaux.append(f"<p><b>Limites :</b> {e(data['limites'])}</p>")
    for i, d in enumerate(data.get("dossiers", []), 1):
        p = _patient(d, patients)
        titre = p.identite() if p else f"Dossier {i} — patient non identifié"
        morceaux.append(f"<h2>{html.escape(titre)}</h2>")
        for cle, lib in (("age_sexe", "Âge / sexe"), ("contexte", "Contexte et imagerie"),
                         ("discussion", "Discussion"), ("decision", "Décision"),
                         ("geste_ri", "Geste de radiologie interventionnelle"),
                         ("suite", "Suite à programmer"), ("incertain", "À vérifier")):
            if d.get(cle):
                morceaux.append(f"<p><b>{lib} :</b> {e(d[cle])}</p>")
    if data.get("missions"):
        morceaux.append("<h2>Missions de l'équipe de radiologie interventionnelle</h2><ul>")
        for m in data["missions"]:
            p = _patient(m, patients)
            qui = f"{p.nom} {p.prenom} — " if p else ""
            morceaux.append(f"<li>{html.escape(qui)}{e(m.get('action'))}</li>")
        morceaux.append("</ul>")
    page = ("<html><head><meta charset='utf-8'><style>body{font-family:Calibri,Arial;font-size:11pt}"
            "h1{font-size:16pt}h2{font-size:13pt;color:#1F4E79;margin-top:14pt}</style></head><body>"
            + "\n".join(morceaux) + "</body></html>")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "rcp.html"
        src.write_text(page, encoding="utf-8")
        subprocess.run(["textutil", "-convert", "docx", "-inputencoding", "UTF-8",
                        "-output", str(sortie), str(src)], check=True, capture_output=True)
    return sortie


def deposer_word_drive(local: Path, dossier_drive: str | None, jour: datetime) -> str:
    from . import dossier
    cible = dossier_drive or f"RCP {jour:%d}／{jour:%m}"
    # Ne JAMAIS écraser un document existant (ex. une synthèse retouchée à la main) : nom libre.
    try:
        existants = {f["Name"] for f in dossier._lsjson(cible, "--files-only")}
    except RuntimeError:
        existants = set()  # dossier pas encore créé
    nom, i = local.name, 2
    while nom in existants:
        nom, i = f"{local.stem} ({i}){local.suffix}", i + 1
    subprocess.run([dossier.rclone(), "copyto", "--ignore-existing", str(local),
                    f"{dossier.RACINE}/{cible}/{nom}"], check=True, capture_output=True, timeout=180)
    return f"{cible}/{nom}"


# ------------------------------------------------- brouillons Mail
SCRIPT_BROUILLON = r'''
on run argv
  set {sujet, corps, expediteur, listeA, listeCc} to {item 1 of argv, item 2 of argv, item 3 of argv, item 4 of argv, item 5 of argv}
  set AppleScript's text item delimiters to ","
  tell application "Mail"
    set m to make new outgoing message with properties {subject:sujet, content:corps, visible:false}
    set sender of m to expediteur
    repeat with a in (text items of listeA)
      if (a as text) is not "" then tell m to make new to recipient at end of to recipients with properties {address:(a as text)}
    end repeat
    repeat with a in (text items of listeCc)
      if (a as text) is not "" then tell m to make new cc recipient at end of cc recipients with properties {address:(a as text)}
    end repeat
    save m
    close m saving yes
  end tell
  return "ok"
end run
'''


def creer_brouillon(mail: dict) -> None:
    r = subprocess.run(["osascript", "-", mail["objet"], mail["corps"], MOI,
                        ",".join(mail["a"]), ",".join(mail["cc"])],
                       input=SCRIPT_BROUILLON, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-200:])


# Synthèse Word de la RCP : brouillon (jamais envoyé) depuis le mail AP-HP, Word en pièce jointe.
WORD_A = ["marie.talabard@aphp.fr"]  # Dr Marie-Pauline Talabard
# Thomas (23/11/2026) : un SEUL brouillon après la RCP, celui de Marie-Pauline. Pas de brouillons secrétariat.
BROUILLONS_SECRETARIAT = False

SCRIPT_BROUILLON_PJ = r'''
on run argv
  set {sujet, corps, expediteur, dest, pj} to {item 1 of argv, item 2 of argv, item 3 of argv, item 4 of argv, item 5 of argv}
  tell application "Mail"
    set m to make new outgoing message with properties {subject:sujet, content:corps & return & return, visible:false}
    set sender of m to expediteur
    tell m to make new to recipient at end of to recipients with properties {address:dest}
    tell content of m to make new attachment with properties {file name:(POSIX file pj)} at after last paragraph
    delay 2
    save m
    close m saving yes
  end tell
  return "ok"
end run
'''


def synthese_courte(data: dict, patients: list, jour: datetime) -> str:
    """Corps du mail : un bloc de 2 lignes par dossier (🎯 = geste pour l'équipe de RI)."""
    lignes = [f"Bonjour Marie-Pauline,", "", f"Synthèse de la RCP du {jour:%d/%m/%Y} :", ""]
    for i, d in enumerate(data.get("dossiers", []), 1):
        p = _patient(d, patients)
        cible = "🎯 " if str(d.get("geste_ri", "")).lower().startswith("oui") else ""
        titre = (p.identite() + " — " if p else "") + reidentifier(str(d.get("titre_court") or d.get("age_sexe") or ""), patients)
        court = reidentifier(str(d.get("court") or d.get("decision") or ""), patients)
        lignes += [f"{i}. {cible}{titre}", f"→ {court}", ""]
    lignes += ["Détail dans le Word ci-joint.", "", "Bien amicalement,", "Thomas"]
    return "\n".join(lignes)


def brouillon_word(word: Path, jour: datetime, a: list[str] = WORD_A, corps: str | None = None) -> None:
    """Brouillon « Synthèse RCP JJ/MM » adressé à Marie-Pauline : synthèse courte + Word joint (Thomas relit et envoie)."""
    corps = corps or (f"Bonjour Marie-Pauline,\n\nCi-joint la synthèse de la RCP du {jour:%d/%m/%Y}.\n\n"
                      "Bien amicalement,\nThomas")
    for dest in a:
        r = subprocess.run(["osascript", "-", f"Synthèse RCP du {jour:%d/%m/%Y}", corps, MOI, dest, str(word)],
                           input=SCRIPT_BROUILLON_PJ, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[-200:])


# ------------------------------------------------- Agenda RI (missions)
def entrees_agenda(data: dict, patients: list[PatientRCP], mails: list[dict], jour: datetime) -> list[dict]:
    """Une entrée par patient dans « Questions perso » (Boîte Mail = RCP), actions regroupées."""
    groupes: dict[str, list[str]] = {}
    for m in data.get("missions", []):
        p = _patient(m, patients)
        cle = p.identite() if p else f"RCP du {jour:%d/%m} — patient non identifié"
        action = reidentifier(str(m.get("action", "")), patients)
        if m.get("echeance"):
            action += f" ({m['echeance']})"
        groupes.setdefault(cle, []).append(action)
    entrees = []
    for sujet, actions in groupes.items():
        n = sum(1 for m in mails if m.get("patient") == sujet)
        entrees.append({
            "Date": f"{jour:%d/%m/%Y}", "Boîte Mail": "RCP", "Expéditeur": f"RCP du {jour:%d/%m}",
            "Email Expéditeur": "", "Sujet / Objet": sujet,
            "Question / Action demandée": " • ".join(actions),
            "Statut": "Non répondu — à faire (missions RCP)",
            "Date Réponse / Note": f"{n} brouillon(s) de mail prêt(s) dans la boîte AP-HP" if n else "",
        })
    return entrees


def _env_agenda() -> dict[str, str]:
    from .agenda import ENV_AGENDA
    env = {}
    if ENV_AGENDA.exists():
        for ligne in ENV_AGENDA.read_text(encoding="utf-8").splitlines():
            k, _, v = ligne.partition("=")
            if v and not k.strip().startswith("#"):
                env[k.strip()] = v.strip()
    return env


def ajouter_missions_agenda(entrees: list[dict]) -> int:
    """Ajoute les missions à l'encart « Questions perso » (lecture du serveur, ajout, réécriture)."""
    from . import agenda
    if not entrees:
        return 0
    env = _env_agenda()
    if not env.get("ADMIN_TOKEN") or not env.get("SHEETS_WEBHOOK_URL"):
        raise RuntimeError("jeton de l'agenda absent (Agendainterventionnel/.env)")
    actuelles = agenda.lire_tout().get("questions_perso") or []
    cle = lambda q: (q.get("Date", ""), " ".join(str(q.get("Sujet / Objet", "")).lower().split()))  # noqa: E731
    deja = {cle(q) for q in actuelles}
    nouvelles = [e for e in entrees if cle(e) not in deja]
    if not nouvelles:
        return 0
    liste = nouvelles + actuelles  # les missions RCP en tête
    corps = json.dumps({"action": "set_personal", "questions_perso": liste,
                        "admin_token": env["ADMIN_TOKEN"]}, ensure_ascii=False)
    r = subprocess.run(["curl", "-4", "-sL", "--max-time", "90", "-H", "Content-Type: text/plain;charset=utf-8",
                        "-d", "@-", env["SHEETS_WEBHOOK_URL"]], input=corps.encode(), capture_output=True, check=True)
    rep = json.loads(r.stdout.decode("utf-8"))
    if rep.get("status") != "ok":
        raise RuntimeError(str(rep)[:200])
    # Copie locale tenue à jour (les autres agents de l'agenda repartent de ce fichier).
    perso = agenda.ENV_AGENDA.parent / "personal_data.json"
    try:
        d = json.loads(perso.read_text(encoding="utf-8")) if perso.exists() else {}
        d["questions_perso"] = liste
        perso.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError) as e:
        cli.log(f"personal_data.json non mis à jour ({e}).")
    return len(nouvelles)


# ------------------------------------------------------------ tout
def suite_rcp(brut: str, jour: datetime, brouillons: bool = True, agenda_ri: bool = True,
              drive: bool = True) -> dict:
    """Word + brouillons + missions. Renvoie un petit bilan (affiché et notifié)."""
    bilan: dict = {}
    texte_liste, dossier_drive = "", None
    try:
        texte_liste, dossier_drive = liste_preparatoire(jour)
        cli.log(f"RCP : liste préparatoire « {dossier_drive} » lue." if dossier_drive
                else "RCP : aucun dossier « RCP JJ/MM » dans Drive (patients non identifiés).")
    except Exception as e:  # noqa: BLE001
        cli.log(f"RCP : liste préparatoire non lue ({e}).")
    patients = lire_patients(texte_liste) if texte_liste else []
    bilan["patients"] = len(patients)
    liste = anonymiser(preparer_liste(texte_liste, patients, jour),
                       [m for p in patients for m in p.mots()]).texte if texte_liste else ""
    data = demander_claude(liste, anonymiser_pour_claude(brut, patients))
    completer_sexe(data, patients)
    mails = mails_finaux(data, patients, jour)
    base = cli.chemin_libre(cli.PRETS, f"Synthèse RCP {jour:%d-%m-%Y}", ".docx")
    try:
        ecrire_word(data, patients, jour, base)
        bilan["word"] = str(base)
        if brouillons:
            try:
                brouillon_word(base, jour, corps=synthese_courte(data, patients, jour))
                cli.log(f"RCP : brouillon (Word joint) préparé pour {', '.join(WORD_A)} — à envoyer par le Dr Lefevre.")
            except Exception as e:  # noqa: BLE001
                cli.log(f"RCP : brouillon Word non créé ({e}).")
        try:
            if drive:
                bilan["drive"] = deposer_word_drive(base, dossier_drive, jour)
        except Exception as e:  # noqa: BLE001
            cli.log(f"RCP : Word non copié dans Drive ({e}).")
    except Exception as e:  # noqa: BLE001
        cli.log(f"RCP : Word impossible ({e}).")
    (cli.PRETS / f"{base.stem}_mails.json").write_text(json.dumps(mails, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
    n = 0
    if brouillons and BROUILLONS_SECRETARIAT:
        for m in mails:
            try:
                creer_brouillon(m)
                n += 1
            except Exception as e:  # noqa: BLE001
                cli.log(f"RCP : brouillon « {m['objet'][:40]} » impossible ({e}).")
    bilan["brouillons"] = n
    if agenda_ri:
        try:
            bilan["missions"] = ajouter_missions_agenda(entrees_agenda(data, patients, mails, jour))
        except Exception as e:  # noqa: BLE001
            cli.log(f"RCP : missions non ajoutées à l'agenda ({e}).")
    cli.log(f"RCP : {bilan}")
    cli.notifier(f"RCP : Word prêt, {n} brouillon(s) dans la boîte AP-HP, "
                 f"{bilan.get('missions', 0)} mission(s) dans l'Agenda RI.")
    return bilan
