import unittest

from ecoute import controle as c


class TestControle(unittest.TestCase):
    def test_message_ok(self):
        t = c.message(True, ["✅ service de traitement actif"], ["→ Novembre archives : RCP 16／11", "+ créé : RCP 23／11"])
        self.assertIn("tout est prêt ✅", t)
        self.assertIn("1 élément(s) archivé(s)", t)
        self.assertIn("+ créé : RCP 23/11", t)
        self.assertIn("🟢 Écoute RCP", t)

    def test_message_echec(self):
        t = c.message(False, ["✅ Claude répond", "❌ Mail ne voit pas le compte AP-HP → ouvrir Mail"], [])
        self.assertIn("À VÉRIFIER", t)
        self.assertIn("❌ Mail ne voit pas le compte AP-HP → ouvrir Mail", t)


if __name__ == "__main__":
    unittest.main()
