import unittest
from datetime import datetime

from ecoute import rcp_suite as r

# Patients fictifs.
LISTE = """DUPONT Jean 8011111111
MARTIN Claire  8022222222
BERNARD Lucie (née le 15/03/1952), prise en charge au CH Versailles
 Mme PETIT Anne-Sophie, née le 05/08/1953 : 8033333333


Nom, Prénom: DUPONT, Jean Pierre Marie (Mr)
Nom de naissance : DUPONT
Date de naissance : 15/02/1996
N° patient, sexe : 8011111111, Masculin
IRM DU RACHIS ENTIER
Lésions secondaires stables.

Nom, Prénom: MARTIN, Claire
Nom de naissance : DURAND
Date de naissance : 29/10/1956
N° patient, sexe : 8022222222, Féminin
TEP : lésion du psoas stable.
"""
JOUR = datetime(2026, 11, 23)


class TestRcpSuite(unittest.TestCase):
    def setUp(self):
        self.ps = r.lire_patients(LISTE)

    def test_lecture_patients(self):
        self.assertEqual([p.nom for p in self.ps], ["DUPONT", "MARTIN", "BERNARD", "PETIT"])
        d, m, b, p = self.ps
        self.assertEqual((d.ipp, d.naissance, d.sexe), ("8011111111", "15/02/1996", "M"))
        self.assertEqual((m.naissance, m.sexe), ("29/10/1956", "F"))
        self.assertIn("DURAND", m.autres_noms)
        self.assertEqual((b.ipp, b.naissance, b.sexe), ("", "15/03/1952", "F"))
        self.assertEqual((p.prenom, p.ipp, p.sexe), ("Anne-Sophie", "8033333333", "F"))
        self.assertEqual(m.identite(), "Mme MARTIN Claire (née le 29/10/1956) — IPP 8022222222")

    def test_aucune_identite_vers_claude(self):
        liste = r.anonymiser(r.preparer_liste(LISTE, self.ps, JOUR),
                             [m for p in self.ps for m in p.mots()]).texte
        trans = r.anonymiser_pour_claude("Madame Martin, 70 ans. Ensuite monsieur Dupon… non, Dupont. "
                                         "Le Dr Bernard a vu madame Petite.", self.ps)
        for texte in (liste, trans):
            for mot in ("DUPONT", "Dupont", "MARTIN", "Martin", "DURAND", "8011111111", "15/02/1996",
                        "Anne-Sophie"):
                self.assertNotIn(mot, texte)
        self.assertIn("⟦P2⟧ : femme, 70 ans", liste)
        self.assertIn("⟦P2⟧", trans)
        self.assertIn("⟦P1⟧", trans)

    def test_mails_et_agenda(self):
        data = {"dossiers": [], "missions": [
            {"patient": "P2", "action": "Programmer une IRM du bassin", "echeance": "1 mois"},
            {"patient": None, "action": "Récupérer la TEP"}],
            "mails": [
                {"patient": "P2", "type": "programmation", "objet": "",
                 "corps": "Bonjour,\n\nPouvez-vous programmer pour {PATIENT} :\n- IRM du bassin\n\nMerci"},
                {"patient": "P1", "type": "correspondant", "objet": "RCP du 23/11 — décision",
                 "corps": "Bonjour,\n\nDécision pour ⟦P1⟧.\n\nMerci"}]}
        mails = r.mails_finaux(data, self.ps, JOUR)
        prog, corr = mails
        self.assertEqual(prog["objet"], "Mme MARTIN Claire (née le 29/10/1956) — IPP 8022222222")
        self.assertIn("pour Mme MARTIN Claire (née le 29/10/1956) — IPP 8022222222 :", prog["corps"])
        self.assertEqual(prog["a"], ["rdv.radiob.cch@aphp.fr"])
        self.assertIn("marie.talabard@aphp.fr", prog["cc"])
        self.assertTrue(prog["corps"].rstrip().endswith("Tél: 33.(0)1.58.41.24.89"))
        self.assertEqual(corr["a"], [])
        self.assertIn("M. DUPONT Jean", corr["corps"])
        entrees = r.entrees_agenda(data, self.ps, mails, JOUR)
        self.assertEqual(len(entrees), 2)
        self.assertEqual(entrees[0]["Boîte Mail"], "RCP")
        self.assertIn("IRM du bassin (1 mois)", entrees[0]["Question / Action demandée"])
        self.assertIn("1 brouillon", entrees[0]["Date Réponse / Note"])

    def test_json_entoure(self):
        d = r.lire_json('```json\n{"dossiers": [{"patient": "P1"}]}\n```')
        self.assertEqual(d["mails"], [])
        self.assertEqual(d["dossiers"][0]["patient"], "P1")

    def test_dossier_rcp(self):
        self.assertTrue(r.est_dossier_rcp("RCP 23／11", JOUR))
        self.assertTrue(r.est_dossier_rcp("rcp méta os 23-11", JOUR))
        self.assertFalse(r.est_dossier_rcp("Consultation 23／11", JOUR))
        self.assertFalse(r.est_dossier_rcp("RCP 16／11", JOUR))


if __name__ == "__main__":
    unittest.main()
