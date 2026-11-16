import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from ecoute.dossier import Document, choisir, concerne, document_du_patient, deja_utilises, est_dossier_du_jour, marquer_utilise

JOUR = datetime(2026, 11, 16, 8, 30)


def doc(i, h, m):
    return Document(i, f"Consultation 16／11/{i}.txt", f"{i}.txt", datetime(2026, 11, 16, h, m))


class TestDossier(unittest.TestCase):
    def test_nom_du_dossier_du_jour(self):
        for nom in ("Consultation 16／11", "Consultation 16 11", "consultations 16-11-2026",
                    "Consultation 16/11"):
            self.assertTrue(est_dossier_du_jour(nom, JOUR), nom)
        for nom in ("Consultation 09／11", "Echo 16 11", "Consultation 6 11", "Consultation 16 1"):
            self.assertFalse(est_dossier_du_jour(nom, JOUR), nom)
        self.assertTrue(est_dossier_du_jour("Consultation 06 11", datetime(2026, 11, 6)))

    def test_plus_recent_avant_la_fin_et_non_utilise(self):
        docs = [doc("a", 8, 20), doc("b", 9, 10), doc("c", 10, 5)]
        self.assertEqual(choisir(docs, datetime(2026, 11, 16, 9, 40), set()).id, "b")
        self.assertEqual(choisir(docs, datetime(2026, 11, 16, 9, 40), {"b"}).id, "a")
        self.assertIsNone(choisir(docs, datetime(2026, 11, 16, 8, 0), set()))

    def test_registre_des_documents_utilises(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(deja_utilises(Path(d), JOUR), set())
            marquer_utilise(Path(d), JOUR, doc("a", 8, 20))
            self.assertEqual(deja_utilises(Path(d), JOUR), {"a"})
            self.assertEqual(deja_utilises(Path(d), datetime(2026, 11, 17)), set())

    def test_document_au_nom_du_patient(self):
        docs = [Document("r", "x/Rousselle", "Rousselle", datetime(2026, 11, 16, 8, 5)),
                Document("b", "x/BETARI, Reem", "BETARI, Reem", datetime(2026, 11, 16, 8, 22)),
                Document("c", "x/Notes du jour", "Notes du jour", datetime(2026, 11, 16, 8, 25))]
        textes = {"r": "kyste", "b": "desmoide", "c": "Madame Cécile DOS SANTOS LOPES, Morton"}
        lire = lambda d: textes[d.id]
        fin = datetime(2026, 11, 16, 9, 0)
        d, t = document_du_patient(JOUR, fin, ["BETARI", "Reem"], docs, lire)
        self.assertEqual(d.id, "b")
        d, t = document_du_patient(JOUR, fin, ["DOS", "SANTOS", "LOPES", "Cécile"], docs, lire)
        self.assertEqual(d.id, "c")  # trouvé dans le texte
        # Aucun document au nom du patient → première consultation, jamais le dossier d'un autre
        self.assertEqual(document_du_patient(JOUR, fin, ["KHEZR"], docs, lire), (None, None))
        self.assertTrue(concerne("Mme Dos Santos", ["SANTOS"]))
        self.assertFalse(concerne("Mme Dos Santos", ["DOS"]))


if __name__ == "__main__":
    unittest.main()
