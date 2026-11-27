"""App-specific Chrome settings and safe release-to-release updates."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import browser_config as chrome
import setup


class Chrome(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.env = self.root / "flatpak/overrides/com.google.Chrome"
        self.flags = self.root / "chrome/chrome-flags.conf"
        self.log = self.root / "state/chrome.json"
        self.enterContext(patch.object(chrome, "paths", return_value=(self.env, self.flags)))
        self.enterContext(patch.object(chrome, "installed", return_value=True))

    def test_round_trip_owned_settings_and_previous_unset(self):
        before = ("# header\r\n[Context]\r\nunset-environment=EGL_FAKE;GTK_A11Y;\r\n"
                  "filesystems=foo;bar;\r\n[Environment]\r\nDISPLAY_OLD=hello\r\n"
                  "GTK_A11Y=previous\r\n[Session Bus Policy]\r\norg.test=talk")
        chrome.write(self.env, before)
        chrome.write(self.flags, "# a comment\n--guest")
        for _ in range(2):
            chrome.configure("on", self.log)
            self.assertEqual(chrome.status(self.log), {"chrome_accessibility": "on", "chrome_managed": True})
            self.assertIn("unset-environment=EGL_FAKE;\r\n", chrome.read(self.env))
        chrome.configure("off", self.log)
        self.assertEqual(chrome.read(self.env), before)
        self.assertEqual(chrome.read(self.flags), "# a comment\n--guest")
        self.assertFalse(self.log.exists())

    def test_preserves_old_flags_and_later_user_edits(self):
        chrome.write(self.flags, chrome.FLAG + " --guest\n")
        chrome.configure("on", self.log)
        chrome.write(self.env, chrome.read(self.env).replace("GTK_A11Y=always", "GTK_A11Y=user") + "OTHER=42\n")
        chrome.configure("off", self.log)
        self.assertEqual(chrome.read(self.env), "[Environment]\nGTK_A11Y=user\nOTHER=42\n")
        self.assertEqual(chrome.read(self.flags), chrome.FLAG + " --guest\n")

    def test_created_files_removed_and_early_manual_settings_not_claimed(self):
        chrome.configure("on", self.log)
        chrome.configure("off", self.log)
        self.assertFalse(self.flags.exists())
        self.assertFalse(self.env.exists())
        chrome.write(self.env, "[Environment]\nGTK_A11Y=always\nACCESSIBILITY_ENABLED=1\n")
        chrome.write(self.flags, chrome.FLAG + "\n")
        chrome.configure("on", self.log)
        self.assertFalse(self.log.exists())
        chrome.configure("off", self.log)
        self.assertEqual(chrome.status(self.log), {"chrome_accessibility": "on", "chrome_managed": False})

    def test_conflicting_renderer_flag_stops_before_first_write(self):
        chrome.write(self.flags, "--disable-renderer-accessibility\n")
        with self.assertRaisesRegex(RuntimeError, "conflicting"):
            chrome.configure("on", self.log)
        self.assertFalse(self.env.exists())
        self.assertFalse(self.log.exists())

    def test_crash_after_first_write_can_be_reverted(self):
        original = chrome.write
        def stop_after_overrides(path, value):
            if path == self.flags:
                raise KeyboardInterrupt()
            original(path, value)
        with patch.object(chrome, "write", side_effect=stop_after_overrides), self.assertRaises(KeyboardInterrupt):
            chrome.configure("on", self.log)
        self.assertTrue(self.log.exists())
        chrome.configure("off", self.log)
        self.assertFalse(self.log.exists())
        self.assertFalse(self.env.exists())


class ManualUpgrade(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = Path(tmp.name)
        self.enterContext(patch.object(setup, "APP", self.app))
        old = b"# verified desktop release\n"
        block = b"def default_button_binding(layout, name, action):\n    return None\n\n"
        for name in ("bridge.py", "chord.py"):
            (self.app / name).write_bytes(old)
        self.enterContext(patch.dict(setup.MANUAL_BASELINES, {"bridge.py": {setup.digest(old)},
                                                              "chord.py": {setup.digest(old)}}))
        self.enterContext(patch.object(setup, "OLD_PROFILE_BLOCK", setup.digest(block)))
        self.profile = self.app / "profile.py"
        self.profile.write_bytes(b"KEYBOARD = 0x90000002\n" + block + b"def watch_refresh():\n    pass\n")
        for name in setup.MANUAL_BASELINES:
            if name not in ("bridge.py", "chord.py", "field_return.py", "browser-field-return.js"):
                path = self.app / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((setup.PACKAGE / "src" / name).read_bytes())
        self.enterContext(patch.dict(setup.MANUAL_BASELINES, {"keyboard.py": {setup.digest(old)}}))
        (self.app / "keyboard.py").write_bytes(old)

    def test_upgrades_only_verified_files_preserving_local_profile_and_settings(self):
        updates = setup.manual_runtime_updates()
        self.assertEqual(set(updates), {"bridge.py", "chord.py", "profile.py", "field_return.py", "browser-field-return.js", "keyboard.py"})
        for name, (_, data) in updates.items():
            (self.app / name).write_bytes(data)
        self.assertIn(b"KEYBOARD = 0x90000002", self.profile.read_bytes())
        self.assertEqual((self.app / "keyboard.py").read_bytes(), (setup.PACKAGE / "src/keyboard.py").read_bytes())
        self.assertEqual(setup.manual_runtime_updates(), {})

    def test_changed_manual_keyboard_is_preserved(self):
        launcher = self.app / "keyboard.py"
        launcher.write_bytes(b"# user-local keyboard\n")
        with self.assertRaisesRegex(RuntimeError, "modified file"):
            setup.manual_runtime_updates()
        self.assertEqual(launcher.read_bytes(), b"# user-local keyboard\n")

    def test_foreign_changed_feature_file_stops_before_any_write(self):
        bridge = self.app / "bridge.py"
        bridge.write_bytes(bridge.read_bytes() + b"# my edits\n")
        with self.assertRaisesRegex(RuntimeError, "modified file"):
            setup.manual_runtime_updates()
        self.assertTrue(bridge.read_bytes().endswith(b"# my edits\n"))
        self.assertFalse((self.app / "field_return.py").exists())
