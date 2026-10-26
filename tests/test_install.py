import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", ROOT / "scripts" / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def test_install_is_idempotent_and_backup_is_outside_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "app" / "skills"
            installer.install(installer.SOURCE, root)
            destination = root / installer.NAME
            self.assertEqual(installer.digest(installer.SOURCE), installer.digest(destination))
            self.assertIn("Already current", installer.install(installer.SOURCE, root))
            (destination / "SKILL.md").write_text("changed")
            with self.assertRaises(ValueError):
                installer.install(installer.SOURCE, root)
            installer.install(installer.SOURCE, root, replace=True)
            backups = list((root.parent / "skill-backups").iterdir())
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "SKILL.md").read_text(), "changed")


if __name__ == "__main__":
    unittest.main()
