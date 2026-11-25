import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import appearance


QML = shutil.which("qml6") or shutil.which("qml") or "/usr/lib/qt6/bin/qml"
CUSTOM = {"preset": "custom", "backgroundStart": "#0F3636", "backgroundEnd": "#432138",
          "keyStart": "#195858", "keyEnd": "#533251", "text": "#F9FFFF",
          "accent": "#FFDB60", "direction": "horizontal"}


class AppearanceTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = self.folder / "config"
        self.enterContext(patch.dict(os.environ, XDG_CONFIG_HOME=str(self.config)))

    def test_system_is_default_and_reads_do_not_create_files(self):
        self.assertEqual(appearance.load()["preset"], "default")
        self.assertIsNone(appearance.load_custom())
        self.assertFalse(self.config.exists())

    def test_custom_survives_preset_switches(self):
        self.assertEqual(appearance.save(dict(CUSTOM, accent="#ffdb60")), CUSTOM)
        for preset in appearance.PRESETS:
            expected = {"preset": preset, **appearance.PRESETS[preset]}
            self.assertEqual(appearance.save({"preset": preset}), expected)
            self.assertEqual(appearance.load(), expected)
            self.assertEqual(appearance.load_custom(), CUSTOM)
        self.assertEqual(appearance.save(appearance.load_custom()), CUSTOM)

    def test_invalid_save_keeps_previous_settings(self):
        appearance.save(CUSTOM)
        for invalid in [None, {"preset": "missing"}, dict(CUSTOM, text="#f00"),
                        dict(CUSTOM, direction="unknown"), dict(CUSTOM, accent="red\n[Extra]")]:
            with self.assertRaises(ValueError):
                appearance.save(invalid)
        self.assertEqual(appearance.load(), CUSTOM)
        appearance._path().write_text("[invalid", encoding="utf-8")
        self.assertEqual(appearance.load()["preset"], "default")

    @unittest.skipUnless(Path(QML).is_file(), "requires the Qt 6 QML runner")
    def test_open_qml_panel_observes_save_and_keeps_text_selection(self):
        appearance.save({"preset": "lagoon"})
        layouts = self.folder / "layouts"
        page = layouts / "fallback"
        shared = layouts / "DecktilStyle"
        page.mkdir(parents=True)
        shared.mkdir()
        (shared / "qmldir").write_text("singleton Palette 1.0 DeckPalette.qml\n")
        shutil.copy(ROOT / "src" / "DeckPalette.qml", shared)
        shutil.copy(ROOT / "src" / "DeckAppearance.qml", page)
        fixture = self.folder / "appearance.qml"
        shutil.copy(ROOT / "tests" / "appearance.qml", fixture)
        env = dict(os.environ, XDG_RUNTIME_DIR=str(self.folder), QT_QPA_PLATFORM="offscreen",
                   QT_QUICK_BACKEND="software", QT_FORCE_STDERR_LOGGING="1")
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
            env.pop(key, None)
        output = self.folder / "qt.log"
        with output.open("w") as log:
            proc = subprocess.Popen([QML, str(fixture)],
                                    env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 8
                while "Decktil palette loaded" not in output.read_text() and time.monotonic() < deadline:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.04)
                self.assertIn("Decktil palette loaded", output.read_text())
                appearance.save(CUSTOM)
                self.assertEqual(proc.wait(timeout=6), 0, output.read_text())
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=3)
