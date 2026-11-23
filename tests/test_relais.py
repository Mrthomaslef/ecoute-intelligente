import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from ecoute import cli, relais


class TestRelais(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = [mock.patch.object(relais, "RELAIS", self.tmp / "icloud"),
                  mock.patch.object(cli, "TRAITES", self.tmp / "traites"),
                  mock.patch.object(cli, "DOSSIER", self.tmp)]
        for p in self.p:
            p.start()
        cli.TRAITES.mkdir()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def test_depot_iphone(self):
        import os, time
        with mock.patch.object(relais, "DEPOT_IPHONE", self.tmp / "iphone"):
            relais.DEPOT_IPHONE.mkdir()
            a = relais.DEPOT_IPHONE / "Nouvel enregistrement 3.m4a"
            a.write_bytes(b"\0" * 500)
            (relais.DEPOT_IPHONE / "notes.txt").write_text("x")
            tailles = {}
            self.assertEqual(relais.depots_iphone(tailles), [])  # 1er passage : taille notée
            self.assertEqual(relais.depots_iphone(tailles), [])  # trop récent (copie peut-être en cours)
            vieux = time.time() - 120
            os.utime(a, (vieux, vieux))
            relais.depots_iphone(tailles)
            self.assertEqual(relais.depots_iphone(tailles), [a])

    def test_fichier_icloud_non_telecharge(self):
        f = self.tmp / "a.m4a"
        f.write_bytes(b"x")
        self.assertTrue(relais.present_localement(f))
        faux = mock.Mock(st_flags=relais.SF_DATALESS)
        with mock.patch.object(Path, "stat", return_value=faux), \
             mock.patch.object(relais.subprocess, "run") as run:
            self.assertFalse(relais.present_localement(f))
            self.assertEqual(run.call_args[0][0][:2], ["brctl", "download"])

    def test_mode(self):
        self.assertFalse(relais.est_enregistreur())
        (self.tmp / ".mode").write_text("enregistreur\n")
        self.assertTrue(relais.est_enregistreur())

    def test_aller_retour(self):
        wav = self.tmp / "en_cours.wav"
        wav.write_bytes(b"RIFF" + b"\0" * 100)
        cible = relais.envoyer(wav, datetime(2026, 11, 16, 8, 48), datetime(2026, 11, 16, 8, 59), "cr")
        self.assertFalse(wav.exists())  # rangé dans _traites du MacBook
        self.assertEqual(len(list(cli.TRAITES.iterdir())), 1)
        self.assertEqual(cible.stem, "2026-11-16_08h48_cr")
        prets = relais.a_traiter()
        self.assertEqual([w.name for w, _ in prets], [cible.name])
        self.assertEqual(prets[0][1]["fin"], "2026-11-16T08:59:00")

        with mock.patch.object(cli, "transcrire_audio", return_value="bonjour"), \
             mock.patch.object(cli, "consultation_vers_agenda", return_value=Path("cr.md")) as c:
            relais.traiter(*prets[0])
        _, kw = c.call_args
        self.assertEqual(kw["fin"], datetime(2026, 11, 16, 8, 59))
        self.assertFalse(kw["poste_local"])
        self.assertEqual(relais.a_traiter(), [])  # fiche supprimée, WAV sorti d'iCloud
        self.assertEqual([f for f in relais.RELAIS.iterdir() if not f.name.startswith(".")], [])

    def test_compression_m4a(self):
        import subprocess
        wav = self.tmp / "vrai.wav"
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                        "/System/Library/Sounds/Glass.aiff", str(wav)], check=True)
        cible = relais.envoyer(wav, datetime(2026, 11, 16, 9, 0), datetime(2026, 11, 16, 9, 5), "cr")
        self.assertEqual(cible.suffix, ".m4a")
        self.assertEqual([w for w, _ in relais.a_traiter()], [cible])

    def test_wav_incomplet_attendu(self):
        relais.RELAIS.mkdir(parents=True)
        (relais.RELAIS / "a.wav").write_bytes(b"x" * 10)
        (relais.RELAIS / "a.wav.json").write_text(json.dumps({"debut": "2026-11-16T08:00:00", "taille": 99}))
        self.assertEqual(relais.a_traiter(), [])


if __name__ == "__main__":
    unittest.main()


class TestEcoutesInterrompues(unittest.TestCase):
    def test_detection(self):
        import os
        import tempfile
        import time
        from pathlib import Path
        from ecoute import relais
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            vieux, recent = d / "2026-11-16_12h02_rcp.wav", d / "2026-11-16_13h09_rcp.wav"
            for f in (vieux, recent):
                f.write_bytes(b"\0" * 1000)
            maintenant = time.time()
            os.utime(vieux, (maintenant - 3600, maintenant - 3600))
            pid = d / ".pid"
            self.assertEqual(relais.ecoutes_interrompues(d, pid, maintenant), [vieux])
            pid.write_text("123")  # écoute en cours : on ne touche à rien
            self.assertEqual(relais.ecoutes_interrompues(d, pid, maintenant), [])


class TestDoubleCanal(unittest.TestCase):
    def test_doublon_ecarte_et_test_non_transcrit(self):
        import json, tempfile
        from datetime import datetime
        from pathlib import Path
        from unittest import mock
        from ecoute import cli, relais
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            rel, dos = d / "Relais", d / "Ecoute"
            rel.mkdir(); (dos / "1_Audio" / "_traites").mkdir(parents=True)
            with mock.patch.object(relais, "RELAIS", rel), mock.patch.object(cli, "DOSSIER", dos), \
                 mock.patch.object(cli, "AUDIO", dos / "1_Audio"), mock.patch.object(cli, "TRAITES", dos / "1_Audio" / "_traites"), \
                 mock.patch.object(cli, "notifier", lambda *a: None), mock.patch.object(relais, "present_localement", lambda p: True):
                a = rel / "2026-11-23_10h00_test.m4a"; a.write_bytes(b"x" * 10)
                (rel / f"{a.name}.json").write_text(json.dumps({"debut": "2026-11-23T10:00:00", "type": "test", "taille": 10}))
                prets = relais.a_traiter()
                self.assertEqual([p[0].name for p in prets], [a.name])
                with mock.patch.object(cli, "transcrire_audio", side_effect=AssertionError("pas de transcription")):
                    self.assertIsNone(relais.traiter(*prets[0]))
                self.assertIn(a.name, relais.deja_recus())
                # Le même fichier arrive ensuite par l'autre canal : écarté, jamais retraité.
                a.write_bytes(b"x" * 10)
                (rel / f"{a.name}.json").write_text(json.dumps({"debut": "2026-11-23T10:00:00", "type": "test", "taille": 10}))
                self.assertEqual(relais.a_traiter(), [])
                self.assertFalse(a.exists())
                self.assertTrue(any((dos / "1_Audio" / "_doublons").iterdir()))


class TestNoteFinale(unittest.TestCase):
    def test_note_dictee_regroupee(self):
        from pathlib import Path
        from ecoute import relais
        a = (Path("a.m4a"), {"debut": "2026-11-23T09:10:00", "fin": "2026-11-23T09:20:00", "type": "cr"})
        note = (Path("b.m4a"), {"debut": "2026-11-23T09:22:00", "fin": "2026-11-23T09:24:00", "type": "cr"})
        autre = (Path("c.m4a"), {"debut": "2026-11-23T10:00:00", "fin": "2026-11-23T10:10:00", "type": "cr"})
        g = relais.grouper([autre, note, a])
        self.assertEqual([[p[0].name for p in x] for x in g], [["a.m4a", "b.m4a"], ["c.m4a"]])


class TestAttenteArrivee(unittest.TestCase):
    def test_date_d_origine_conservee_ne_court_circuite_pas_l_attente(self):
        import json, os, tempfile, time
        from pathlib import Path
        from unittest import mock
        from ecoute import cli, relais
        with tempfile.TemporaryDirectory() as d:
            rel = Path(d)
            a = rel / "2026-11-23_09h22_cr.m4a"; a.write_bytes(b"x" * 10)
            f = rel / f"{a.name}.json"; f.write_text(json.dumps({"debut": "2026-11-23T09:22:00", "taille": 10}))
            vieux = time.time() - 3600
            os.utime(f, (vieux, vieux)); os.utime(a, (vieux, vieux))  # comme rclone : date d'origine gardée
            with mock.patch.object(relais, "RELAIS", rel), mock.patch.object(relais, "present_localement", lambda p: True), \
                 mock.patch.object(cli, "DOSSIER", rel):
                self.assertEqual(relais.a_traiter(anciennete_min=180), [])  # arrivé à l'instant : on attend
