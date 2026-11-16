import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from ecoute.dossier import Document, document_du_patient
from ecoute.modeles import decouper_lettres, est_cr_valide, exemples, masquer

L1 = ("Cher confrère, chère consœur,\n\nJe me suis entretenu ce jour avec Monsieur Adrien ROUSSELLE, âgé de 22 ans "
      "(né le 04/08/2004), dans le cadre du suivi d'un kyste osseux anévrismal. " + "Évolution favorable. " * 15 +
      "\n\nBien confraternellement,")
L2 = L1.replace("Monsieur Adrien ROUSSELLE", "Madame Reem BETARI").replace("kyste osseux anévrismal", "tumeur desmoïde")


class TestModeles(unittest.TestCase):
    def test_nom_du_document(self):
        self.assertTrue(est_cr_valide("CR consultation"))
        self.assertTrue(est_cr_valide("CR consultations 16-11.docx"))
        self.assertFalse(est_cr_valide("CR pré op KOA"))
        self.assertFalse(est_cr_valide("Rousselle"))

    def test_decoupe_et_masque(self):
        lettres = decouper_lettres(L1 + "\n\n\n\n" + L2)
        self.assertEqual(len(lettres), 2)
        m = masquer(lettres[0])
        self.assertNotIn("ROUSSELLE", m)
        self.assertNotIn("04/08/2004", m)
        self.assertIn("[PATIENT]", m)

    def test_exemples_recents_et_cache(self):
        docs = [Document("a", "Consultation 09 11/CR consultation", "CR consultation", datetime(2026, 11, 9, 12)),
                Document("b", "Consultation 16 11/CR consultation", "CR consultation", datetime(2026, 11, 16, 12)),
                Document("c", "Consultation 16 11/Rousselle", "Rousselle", datetime(2026, 11, 16, 9))]
        textes = {"a": L1, "b": L1 + "\n\n" + L2}
        lus = []
        lire = lambda d: (lus.append(d.id), textes[d.id])[1]
        with tempfile.TemporaryDirectory() as t:
            ex = exemples(Path(t), 2, docs=docs, lecteur=lire)
            self.assertEqual(len(ex), 2)
            self.assertIn("desmoïde", ex[0])  # dernière lettre du document le plus récent d'abord
            self.assertEqual(sorted(lus), ["a", "b"])  # le document patient n'est pas lu
            exemples(Path(t), 2, docs=docs, lecteur=lire)
            self.assertEqual(sorted(lus), ["a", "b"])  # cache : rien n'est relu

    def test_cr_valide_jamais_dossier_anterieur(self):
        docs = [Document("b", "x/CR consultation", "CR consultation", datetime(2026, 11, 16, 12))]
        res = document_du_patient(datetime(2026, 11, 16, 8), datetime(2026, 11, 16, 13), ["ROUSSELLE"],
                                  docs, lambda d: L1)
        self.assertEqual(res, (None, None))


if __name__ == "__main__":
    unittest.main()
