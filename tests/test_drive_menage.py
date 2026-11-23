import unittest
from datetime import datetime

from ecoute.drive_menage import date_du_nom

J = datetime(2026, 11, 23)


class TestDate(unittest.TestCase):
    def test_noms(self):
        self.assertEqual(date_du_nom("Consultation 16／11", J), datetime(2026, 11, 16))
        self.assertEqual(date_du_nom("CR OP 03／11", J), datetime(2026, 11, 3))
        self.assertEqual(date_du_nom("Consultation 09／11.docx", J), datetime(2026, 11, 9))
        self.assertEqual(date_du_nom("RCP 23／11", J), datetime(2026, 11, 23))
        self.assertEqual(date_du_nom("Echo 28／12", datetime(2027, 1, 4)), datetime(2026, 12, 28))

    def test_non_dates(self):
        for nom in ("IRM novembre", "Novembre archives", "Modèles CR", "CR post op KOA.docx",
                    "Décembre archives 2025"):
            self.assertIsNone(date_du_nom(nom, J), nom)
