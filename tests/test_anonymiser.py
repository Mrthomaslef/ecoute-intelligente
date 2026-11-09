import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ecoute.anonymiser import anonymiser  # noqa: E402


class TestAnonymiser(unittest.TestCase):
    def test_civilite_et_nom(self):
        r = anonymiser("On discute du dossier de Madame Dupont-Martin, 54 ans.")
        self.assertNotIn("Dupont", r.texte)
        self.assertIn("Madame [NOM]", r.texte)
        self.assertIn("54 ans", r.texte)

    def test_nom_compose(self):
        r = anonymiser("Monsieur Jean Durand présente un ostéome ostéoïde.")
        self.assertNotIn("Durand", r.texte)
        self.assertNotIn("Jean", r.texte)
        self.assertIn("ostéome ostéoïde", r.texte)

    def test_date_naissance(self):
        r = anonymiser("Patiente née le 12 mars 1971, suivie pour sarcome.")
        self.assertNotIn("1971", r.texte)
        r2 = anonymiser("né le 03/04/1988")
        self.assertNotIn("1988", r2.texte)

    def test_numeros(self):
        r = anonymiser("IPP 8012345678, tel 06 12 34 56 78, mail a.b@aphp.fr")
        self.assertEqual(r.texte, "IPP [NUMERO], tel [TEL], mail [EMAIL]")

    def test_numero_en_lettres(self):
        r = anonymiser("IP 8 milliards 12 millions 345 mille 678. Homme de 34 ans")
        self.assertEqual(r.texte, "IP [NUMERO]. Homme de 34 ans")
        r2 = anonymiser("son numéro de sécu est un quatre-vingt douze, merci")
        self.assertNotIn("douze", r2.texte)

    def test_conserve_clinique(self):
        t = "Lésion de 12 mm du talus, cryoablation sous AG, contrôle IRM à 3 mois."
        r = anonymiser(t)
        self.assertEqual(r.texte, t)

    def test_liste_locale(self):
        r = anonymiser("le dossier martin est prioritaire", ["Martin"])
        self.assertNotIn("martin", r.texte)

    def test_suspects(self):
        r = anonymiser("Le dossier de Lebrun doit repasser en RCP.")
        self.assertIn("Lebrun", r.a_verifier)
        self.assertNotIn("RCP", r.a_verifier)


if __name__ == "__main__":
    unittest.main()
