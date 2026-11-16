import subprocess
import tempfile
import unittest
from pathlib import Path

from ecoute.rcp import preparer


class TestRcp(unittest.TestCase):
    def test_recollage_et_nettoyage(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for nom in ("a.wav", "b.wav"):
                subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@44100", "-c", "1",
                                "/System/Library/Sounds/Glass.aiff", str(d / nom)], check=True)
            sortie = preparer([d / "a.wav", d / "b.wav"], d / "out.wav")
            info = subprocess.run(["afinfo", str(sortie)], capture_output=True, text=True).stdout
            self.assertIn("16000 Hz", info)
            duree = float(info.split("estimated duration:")[1].split()[0])
            self.assertAlmostEqual(duree, 3.3, delta=0.2)  # 2 × 1,65 s


if __name__ == "__main__":
    unittest.main()
