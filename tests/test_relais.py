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
