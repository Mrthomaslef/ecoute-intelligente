"""Pont vers l'Agenda RI Cochin (Google Sheet, onglet « Consultations »).

- Lit les consultations du jour (patient, horaire, médecin).
- Retrouve le patient de la consultation enregistrée : nom entendu dans la transcription
  brute (comparaison locale), sinon créneau horaire le plus proche.
- Dépose le CR rédigé dans « Remarques de suivi » (colonne K) et dans le champ
  « Suivi clinique & recommandations CS » de la fiche patient de l'app.

Les noms ne quittent jamais le Mac vers Claude : seule l'identité locale est réinsérée
dans le CR après rédaction, puis envoyée à l'agenda (qui la contient déjà).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

WEBHOOK_DEFAUT = ("https://script.google.com/macros/s/AKfycbxLGqUevVWi36mNQ7WNoY2uhRsYLdsG_gQcscMPkP1GcQ"
                  "Et0LgdhGIuzYL2SM-uTcPq/exec")
ENV_AGENDA = Path.home() / "Documents" / "Agendainterventionnel" / ".env"
TROUSSEAU = "ecoute-agenda-cochin"
MEDECIN = "lefevre"
FENETRE_MIN = 120  # écart maximal entre l'enregistrement et le créneau prévu
LIMITE_CHAMP = 4900  # le champ de l'agenda est limité à 5000 caractères


@dataclass
class Consultation:
    row: int
    date: str  # JJ/MM/AAAA
    horaire: str
    patient: str
    medecin: str
    titre: str

    @property
    def iso(self) -> str:
        j, m, a = self.date.split("/")
        return f"{a}-{m}-{j}"

    def minutes(self) -> int | None:
        m = re.match(r"\s*(\d{1,2})\s*[h:]\s*(\d{2})?", self.horaire)
        return int(m.group(1)) * 60 + int(m.group(2) or 0) if m else None


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def norm_nom(s: str) -> str:
    """Équivalent de normName() de l'app (clé de la fiche patient)."""
    s = re.sub(r"\b(?:mr|mme|mlle|madame|monsieur|dr|pr)\.?(?=\s|$)|\bm\.(?=\s|$)", "", norm(s))
    return re.sub(r"[^a-z0-9]", "", s)


def mots_du_nom(patient: str) -> list[str]:
    """Mots du nom (sans civilité ni parenthèses), utilisables pour la recherche et le masquage."""
    s = re.sub(r"\(.*?\)", " ", patient)
    mots = re.findall(r"[A-Za-zÀ-ÿ'’-]{3,}", s)
    return [m for m in mots if norm(m) not in {"mme", "mlle", "madame", "monsieur", "dr", "pr", "epouse", "nee"}]


# ------------------------------------------------------------------ accès
def _identifiants() -> tuple[str, str]:
    url, code = os.environ.get("ECOUTE_AGENDA_URL", ""), os.environ.get("ECOUTE_AGENDA_CODE", "")
    if ENV_AGENDA.exists():
        for ligne in ENV_AGENDA.read_text(encoding="utf-8").splitlines():
            k, _, v = ligne.partition("=")
            if k.strip() == "SHEETS_WEBHOOK_URL" and not url:
                url = v.strip()
            elif k.strip() == "APP_ACCESS_CODE" and not code:
                code = v.strip()
    if not code:
        r = subprocess.run(["security", "find-generic-password", "-s", TROUSSEAU, "-w"],
                           capture_output=True, text=True)
        code = r.stdout.strip()
    if not code:
        code = demander_code()
    return url or WEBHOOK_DEFAUT, code


def demander_code() -> str:
    """Demande une seule fois le code d'accès de l'agenda et le range dans le Trousseau."""
    script = ('text returned of (display dialog "Code d\'accès de votre Agenda RI Cochin '
              '(demandé une seule fois, gardé dans le Trousseau de ce Mac) :" default answer "" '
              'with hidden answer with title "Écoute intelligente")')
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    code = r.stdout.strip()
    if not code:
        raise RuntimeError("code d'accès de l'agenda non saisi")
    subprocess.run(["security", "add-generic-password", "-U", "-s", TROUSSEAU,
                    "-a", "agenda", "-w", code], check=False)
    return code


def executer(code_js: str) -> object:
    url, jeton = _identifiants()
    corps = json.dumps({"eval_code": code_js, "token": jeton}, ensure_ascii=False)
    r = subprocess.run(
        ["curl", "-4", "-sL", "--retry", "2", "--max-time", "90",
         "-H", "Content-Type: text/plain;charset=utf-8", "-d", "@-", url],
        input=corps.encode("utf-8"), capture_output=True, check=True,
    )
    rep = json.loads(r.stdout.decode("utf-8"))
    if rep.get("status") == "unauthorized":
        subprocess.run(["security", "delete-generic-password", "-s", TROUSSEAU], capture_output=True)
        raise RuntimeError("code d'accès de l'agenda refusé (il sera redemandé)")
    if "evalResult" not in rep:
        raise RuntimeError(f"réponse inattendue de l'agenda : {str(rep)[:200]}")
    return rep["evalResult"]


# ------------------------------------------------------------ lecture
def consultations_du(jour: datetime) -> list[Consultation]:
    date = f"{jour:%d/%m/%Y}"
    lignes = executer(f"""
      var v = ss.getSheetByName('Consultations').getDataRange().getDisplayValues();
      var out = [];
      for (var i = 8; i < v.length; i++) {{
        if (String(v[i][1]).trim() === {json.dumps(date)} && String(v[i][3]).trim())
          out.push([i + 1, v[i][1], v[i][2], v[i][3], v[i][5], v[i][6]]);
      }}
      return out;
    """)
    return [Consultation(int(r[0]), *(str(x).strip() for x in r[1:])) for r in lignes]


# ------------------------------------------------------------ appariement
def choisir(consultations: list[Consultation], debut: datetime, texte_brut: str) -> Consultation | None:
    if not consultations:
        return None
    miennes = [c for c in consultations if MEDECIN in norm(c.medecin)] or consultations
    # 1. Nom du patient prononcé pendant la consultation (comparaison locale)
    texte = " " + re.sub(r"[^a-z]+", " ", norm(texte_brut)) + " "
    cites = [c for c in consultations
             if any(f" {norm(m)} " in texte for m in mots_du_nom(c.patient) if len(m) >= 4)]
    if len(cites) == 1:
        return cites[0]
    # 2. Créneau horaire le plus proche du début de l'enregistrement
    candidats = cites or miennes
    t = debut.hour * 60 + debut.minute
    proches = sorted(
        (abs(c.minutes() - t), c) for c in candidats if c.minutes() is not None
    )
    if proches and proches[0][0] <= FENETRE_MIN:
        return proches[0][1]
    return candidats[0] if len(candidats) == 1 else None


# ------------------------------------------------------------- écriture
def deposer(c: Consultation, cr: str, essai: bool = False) -> str:
    """Ajoute le CR à la fiche (sans effacer l'existant). Renvoie la clé de fiche."""
    entete = f"— CR de consultation dicté le {datetime.now():%d/%m/%Y à %Hh%M} —"
    cle = f"{c.iso}_{norm_nom(c.patient)}_cs"
    resultat = executer(f"""
      var row = {c.row}, pat = {json.dumps(c.patient)}, cle = {json.dumps(cle)};
      var bloc = {json.dumps(entete + chr(10) + cr.strip())};
      var sh = ss.getSheetByName('Consultations');
      function n(s) {{ return String(s || '').toLowerCase().replace(/[^a-z0-9]/g, ''); }}
      if (n(sh.getRange(row, 4).getDisplayValue()) !== n(pat)) return {{ ok: false, raison: 'ligne déplacée' }};
      var props = PropertiesService.getScriptProperties();
      var cur = JSON.parse(props.getProperty('cochin_suivi_bloc_v1') || '{{}}');
      var ent = cur[cle] || {{ suivi_key: cle, row: row, patient_name: pat, ipp: '', iso_date: {json.dumps(c.iso)},
                              category: 'cs', title: {json.dumps(c.titre)}, materiel: '', remarques: '', anapath: '' }};
      var avant = ent.suivi_cs || sh.getRange(row, 11).getDisplayValue() || '';
      var texte = (avant ? avant + '\\n\\n' : '') + bloc;
      if (texte.length > 5000) texte = bloc.slice(0, 5000);
      if ({'true' if essai else 'false'}) return {{ ok: true, essai: true, longueur: texte.length, avant: avant.length }};
      ent.suivi_cs = texte; ent.row = row; ent.updated_at = new Date().toISOString();
      cur[cle] = ent;
      props.setProperty('cochin_suivi_bloc_v1', JSON.stringify(cur));
      sh.getRange(row, 11).setValue(texte);
      return {{ ok: true }};
    """)
    if essai:
        return json.dumps(resultat)
    if not (isinstance(resultat, dict) and resultat.get("ok")):
        raise RuntimeError(f"dépôt refusé par l'agenda : {resultat}")
    return cle


def reidentifier(cr: str, c: Consultation) -> str:
    """Remet localement l'identité du patient à la place de la balise [PATIENT]."""
    cr = cr.replace("[PATIENT]", c.patient)
    return cr if len(cr) <= LIMITE_CHAMP else cr[:LIMITE_CHAMP].rsplit("\n", 1)[0] + "\n[…] (CR complet dans 4_CR_Prets)"
