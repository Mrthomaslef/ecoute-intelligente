import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ecoute.cli import corriger  # noqa: E402
from ecoute.live import Segmenteur, TAUX, TRAME  # noqa: E402
import numpy as np  # noqa: E402


class TestCorrections(unittest.TestCase):
    def test_molecules(self):
        self.assertEqual(corriger("Je prends du cardégique 75", {"cardégique": "Kardégic"}),
                         "Je prends du Kardégic 75")

    def test_mot_partiel_intact(self):
        self.assertEqual(corriger("stirage", {"stir": "STIR"}), "stirage")


class TestSegmenteur(unittest.TestCase):
    def test_decoupe_sur_silence(self):
        rng = np.random.default_rng(0)
        silence = rng.normal(0, 0.001, TAUX).astype(np.float32)
        voix = (0.2 * np.sin(np.linspace(0, 2000, 2 * TAUX))).astype(np.float32)
        flux = np.concatenate([silence, voix, silence, voix, silence])
        seg, phrases = Segmenteur(), []
        for i in range(0, len(flux), TRAME):
            p = seg.ajouter(flux[i:i + TRAME])
            if p is not None:
                phrases.append(len(p) / TAUX)
        self.assertEqual(len(phrases), 2)
        self.assertTrue(all(2.0 <= d <= 3.2 for d in phrases), phrases)


if __name__ == "__main__":
    unittest.main()
