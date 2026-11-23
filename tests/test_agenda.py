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
        self.assertEqual(reidentifier("J'ai reçu [PATIENT].", JOUR[0]), "J'ai reçu Madame Claire DUPONTEL.")


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


class TestReidentifier(unittest.TestCase):
    def test_civilite_prenom_nom_et_ddn(self):
        from ecoute.agenda import Consultation, nom_civil, reidentifier
        self.assertEqual(nom_civil("Mme SUPA KANZI Julienne"), "Madame Julienne SUPA KANZI")
        self.assertEqual(nom_civil("COEUILLE Frederic", fem=False), "Monsieur Frederic COEUILLE")
        c = Consultation(1, "23/11/2026", "08h50", "Mme SUPA KANZI Julienne", "TL", "Télécs")
        cr = "J'ai revu ce jour [PATIENT], âgée de 64 ans (née le [DATE]), un mois après."
        self.assertEqual(reidentifier(cr, c, ddn="05/05/1962"),
                         "J'ai revu ce jour Madame Julienne SUPA KANZI, âgée de 64 ans (née le 05/05/1962), un mois après.")
        self.assertEqual(reidentifier(cr, c), "J'ai revu ce jour Madame Julienne SUPA KANZI, âgée de 64 ans, un mois après.")


class TestPatientEnAvance(unittest.TestCase):
  def test_patient_en_avance_reconnu_par_le_motif(self):
      """23/11 : Mme Rebequet (11h00) vue à 10h11 sans dire son nom → rangée chez Boulbetateche (10h00)."""
      from datetime import datetime
      from ecoute.agenda import Consultation, choisir
      cs = [Consultation(49, "23/11/2026", "10h00", "BOULBETATECHE Samira", "Dr LEFEVRE Thomas", "Consultation pré-cryo névrome de Morton droit"),
            Consultation(50, "23/11/2026", "10h30", "Mme LAFONT Estelle", "Dr LEFEVRE Thomas", "CS pré-interventionnelle (RF ostéome ostéoïde col fémoral G)", "CS anesth 15/12"),
            Consultation(51, "23/11/2026", "11h00", "REBEQUET Joceline", "Dr LEFEVRE Thomas", "Consultation spécialisée", "Évaluation cryoneurolyse post-zona (bloc test nerf intercostal +)")]
      texte = "depuis le zona j'ai mal sous le sein, le bloc test a marché, on va traiter le nerf intercostal, pas d'anesthésiste"
      assert choisir(cs, datetime(2026, 11, 23, 10, 11), texte, {"boulbetatechesamira"}).row == 51
      # sans indice de contenu : le patient déjà fait est écarté, créneau le plus proche parmi les autres
      assert choisir(cs, datetime(2026, 11, 23, 10, 11), "bonjour asseyez-vous", {"boulbetatechesamira"}).row == 50
      # nom prononcé : prioritaire
      assert choisir(cs, datetime(2026, 11, 23, 10, 11), "madame lafont bonjour", {"boulbetatechesamira"}).row == 50


class TestNomMalOrthographie(unittest.TestCase):
    def test_laffont_pour_lafont(self):
        """23/11 : « Madame Laffont » (note de 11h23) rangée chez Bouvrais (créneau 11h30)."""
        from datetime import datetime
        from ecoute.agenda import Consultation, choisir
        cs = [Consultation(50, "23/11/2026", "10h30", "Mme LAFONT Estelle", "Dr LEFEVRE Thomas", "CS pré-interventionnelle (RF ostéome ostéoïde col fémoral G)", "CS anesth 15/12"),
              Consultation(44, "23/11/2026", "11h30", "Mme BOUVRAIS Valérie", "Dr LEFEVRE Thomas", "CS de réévaluation (reliquat d'ostéoblastome branche ilio-pubienne D)", "Expliquer le traitement percutané et évaluer l'acceptation du risque sur le nerf obturateur.")]
        texte = "Je complète pour Madame Laffont si l'enregistrement n'est pas passé. ostéome du col, radiofréquence, je lui ai expliqué les risques, traitement par kétoprofène"
        self.assertEqual(choisir(cs, datetime(2026, 11, 23, 11, 23), texte).row, 50)
        texte2 = "Je complète pour madame, ostéome ostéoïde du col fémoral, radiofréquence, consultation d'anesthésie, je lui ai expliqué les risques et le traitement"
        self.assertEqual(choisir(cs, datetime(2026, 11, 23, 11, 23), texte2).row, 50)


class TestDateCitee(unittest.TestCase):
    def test_fin_de_consultation_reconnue_par_la_date(self):
        """23/11 : fin de la CS Lafont (11h17, « j'ai le 5 janvier 7h30 ») rangée chez Chemla (11h30)."""
        from datetime import datetime
        from ecoute.agenda import Consultation, choisir
        cs = [Consultation(50, "23/11/2026", "10h30", "Mme LAFONT Estelle", "Dr LEFEVRE Thomas", "CS pré-interventionnelle (RF ostéome ostéoïde col fémoral G)", "CS anesth 15/12 · RF sous AG 05/01/2027"),
              Consultation(53, "23/11/2026", "11h30", "Mme CHEMLA Toscane", "Dr LEFEVRE Thomas", "CS pré-op (cryo récidive TCG tibia proximal / genou)", "CS anesth 14h00. Cryo 01/12 08h30.")]
        texte = "ça dure combien de temps l'opération, la prescription je vous l'envoie par mail, j'ai le 5 janvier 7h30"
        self.assertEqual(choisir(cs, datetime(2026, 11, 23, 11, 17), texte, {"lafontestelle"}).row, 50)
