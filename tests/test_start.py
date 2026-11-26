"""Check that the app-menu launcher survives removal of the downloaded archive."""
import runpy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import start


class MenuInstallTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = root / "unpacked release"
        self.data = root / "app data"
        self.source.mkdir()
        for name in (start.LAUNCHER, "appearance_page.py", "browser_config.py", "setup.py", "steam_shortcuts.py", "INSTALL.md", "LICENSE"):
            shutil.copyfile(start.SOURCE / name, self.source / name)
        shutil.copytree(start.SOURCE / "src", self.source / "src", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(start.SOURCE / "experimental/gaming", self.source / "experimental/gaming",
                        ignore=shutil.ignore_patterns("__pycache__", "test_*.py"))
        (self.source / "manager.py").write_text("# first version\n")

    def test_reopen_and_update_from_download(self):
        first, menu = start.install_manager(self.source, self.data)
        self.assertEqual(start.install_manager(self.source, self.data), (first, menu))
        self.assertIn(f'Exec=/usr/bin/python3 "{first}"', menu.read_text())
        (self.source / "manager.py").write_text("# second version\n")
        second, new_menu = start.install_manager(self.source, self.data)
        self.assertNotEqual(first, second)
        self.assertEqual(menu, new_menu)
        shutil.rmtree(self.source)
        self.assertEqual(first.read_text(), "# first version\n")
        self.assertIn(f'Exec=/usr/bin/python3 "{second}"', menu.read_text())
        self.assertTrue((second.parent / "src/words/ru.txt.gz").is_file())
        # Exercise the actual Gaming Mode builder after removing the release folder.
        gaming = second.parent / "experimental/gaming"
        snapshot = self.data / "gaming snapshot"
        snapshot.mkdir()
        runpy.run_path(str(gaming / "install-pilot.py"))["build"](snapshot)
        self.assertTrue((snapshot / "experimental/gaming/panel.qml").is_file())
        self.assertTrue((snapshot / "src/DeckGameWordRow.qml").is_file())
        for unit in ("decktil-bigpicture.service", "decktil-gaming-pilot.service"):
            self.assertTrue((gaming / unit).is_file())

    def test_incomplete_download_preserves_working_menu(self):
        manager, menu = start.install_manager(self.source, self.data)
        previous = menu.read_bytes()
        (self.source / "src/DeckSelector.qml").unlink()
        with self.assertRaises(FileNotFoundError):
            start.install_manager(self.source, self.data)
        self.assertEqual(menu.read_bytes(), previous)
        self.assertTrue(manager.is_file())

    def test_partial_gaming_download_preserves_working_menu(self):
        manager, menu = start.install_manager(self.source, self.data)
        (self.source / "experimental/gaming/search_space.py").unlink()
        with self.assertRaises(FileNotFoundError):
            start.install_manager(self.source, self.data)
        self.assertIn(str(manager), menu.read_text())

    def test_unrelated_menu_entry_is_preserved(self):
        menu = self.data / "applications" / start.MENU_ID
        menu.parent.mkdir(parents=True)
        menu.write_text("existing entry")
        with self.assertRaises(RuntimeError):
            start.install_manager(self.source, self.data)
        self.assertEqual(menu.read_text(), "existing entry")
