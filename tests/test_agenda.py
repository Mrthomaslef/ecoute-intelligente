import unittest
from datetime import datetime

from ecoute.agenda import Consultation, choisir, mots_du_nom, norm_nom, reidentifier

TL = "Dr LEFEVRE Thomas Jean (TL)"
JOUR = [
    Consultation(10, "09/11/2026", "09h00", "Mme DUPONTEL Claire", TL, "Consultation"),
    Consultation(11, "09/11/2026", "09h30", "M. KERVARECH Yann", TL, "Consultation pré-op"),
    Consultation(12, "09/11/2026", "11h00", "Mme ABADIE Rose", "Dr MONNIER Hippolyte (HM)", "CS"),
]


class TestAgenda(unittest.TestCase):
    def test_nom_prononce_prioritaire(self):
        c = choisir(JOUR, datetime(2026, 11, 9, 9, 2), "bonjour monsieur Kervaréch, alors ce genou")
        self.assertEqual(c.row, 11)

    def test_creneau_le_plus_proche_parmi_mes_consultations(self):
        c = choisir(JOUR, datetime(2026, 11, 9, 9, 40), "bonjour, alors cette douleur")
        self.assertEqual(c.row, 11)
        c = choisir(JOUR, datetime(2026, 11, 9, 10, 55), "bonjour")
        self.assertEqual(c.row, 11)  # 11h00 est celle d'un autre médecin

    def test_aucun_creneau_proche(self):
        self.assertIsNone(choisir(JOUR, datetime(2026, 11, 9, 18, 0), "bonjour"))
        self.assertIsNone(choisir([], datetime(2026, 11, 9, 9, 0), "x"))

    def test_cle_fiche_identique_a_l_app(self):
        self.assertEqual(norm_nom("Mme DUPONTEL Claire"), "dupontelclaire")
        self.assertEqual(JOUR[0].iso, "2026-11-09")

    def test_mots_du_nom_sans_civilite(self):
        self.assertEqual(mots_du_nom("Mme DUPONTEL Claire (née MARTIN)"), ["DUPONTEL", "Claire"])

    def test_reidentification_locale(self):
        self.assertEqual(reidentifier("J'ai reçu [PATIENT].", JOUR[0]), "J'ai reçu Mme DUPONTEL Claire.")


if __name__ == "__main__":
    unittest.main()
