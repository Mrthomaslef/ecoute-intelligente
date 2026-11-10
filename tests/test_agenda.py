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


class TestApiSecurisee(unittest.TestCase):
    """Script de l'agenda sécurisé : code dynamique refusé → actions read_all / save_suivi."""

    def setUp(self):
        from ecoute import agenda
        self.agenda, self.envois = agenda, []
        donnees = {
            "Consultations": [{"_row": 11, "Date": "09/11/2026", "Horaire": "09h30",
                               "Identité Patient": "M. KERVARECH Yann", "Médecin en charge": TL,
                               "Type d'acte / Consultation": "CS", "Remarques de suivi": "ancien"}],
            "actes": [{"Identité Patient": "M. KERVARECH Yann", "Matériel utilisé": "aiguille 13G",
                       "Remarques (Bloc)": "RAS"}],
            "Biopsies": [], "suivi_bloc": {},
        }

        def poster(charge):
            self.envois.append(charge)
            if "eval_code" in charge:
                return {"status": "forbidden"}
            if charge.get("action") == "read_all":
                return {"status": "ok", "evalResult": donnees}
            return {"status": "ok", "result": {"updated": ["Consultations:11", "actes:4"]}}

        self.ancien, agenda.poster = agenda.poster, poster

    def tearDown(self):
        self.agenda.poster = self.ancien

    def test_lecture_et_depot_sans_ecraser(self):
        cs = self.agenda.consultations_du(datetime(2026, 11, 9))
        self.assertEqual([(c.row, c.horaire) for c in cs], [(11, "09h30")])
        self.agenda.deposer(cs[0], "Nouveau CR")
        ent = self.envois[-1]["entry"]
        self.assertEqual(self.envois[-1]["action"], "save_suivi")
        self.assertEqual(ent["materiel"], "aiguille 13G")  # conservé
        self.assertEqual(ent["remarques"], "RAS")
        self.assertTrue(ent["suivi_cs"].startswith("ancien\n\n— CR de consultation dicté"))
        self.assertTrue(ent["suivi_cs"].endswith("Nouveau CR"))
        self.assertEqual(ent["suivi_key"], "2026-11-09_kervarechyann_cs")
