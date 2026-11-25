"""Exercise reversible user modes with temporary files and a simulated Plasma session."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import setup


class Modes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        app = root / "share/deck-plasma-keyboard"
        unit = root / "config/systemd/user" / setup.UNIT_NAME
        template = root / "steam/templates/keyboard.vdf"
        entry = root / "share/applications/local.deck.PlasmaKeyboard.desktop"
        state_dir = root / "state"
        self.enterContext(patch.multiple(setup, APP=app, UNIT=unit, ENTRY=entry, TEMPLATE=template,
                                         HELPER=app/"Deck Plasma Keyboard Controls", MANIFEST=state_dir/"install.json",
                                         STATE_DIR=state_dir, ROOT=root/"steam"))
        for path in (unit, entry, template):
            path.parent.mkdir(parents=True, exist_ok=True)
        app.mkdir(parents=True)
        self.enterContext(patch.object(setup, "desktop_session", return_value=True))
        self.current = {"active": True, "enabled": True, "method": str(entry),
                        "policy": {"mode": 1, "raw": None}}
        self.available = True
        self.guard = True
        self.enterContext(patch.object(setup, "unit_state", side_effect=lambda:
                                      (self.current["active"], self.current["enabled"])))
        self.enterContext(patch.object(setup, "method", side_effect=lambda: self.current["method"]))
        self.set_method = self.enterContext(patch.object(setup, "set_method",
                  side_effect=lambda m: self.current.update(method=m)))
        self.enterContext(patch.object(setup, "policy_now", side_effect=lambda: self.current["policy"].copy()))
        self.enterContext(patch.object(setup, "set_policy", side_effect=lambda p: self.current.update(policy=p.copy())))
        self.enterContext(patch.object(setup, "panel_state", side_effect=lambda:
                           {"mode": self.current["policy"]["mode"], "visible": False, "available": self.available}))
        self.enterContext(patch.object(setup, "unload_popup_guard", side_effect=self.clear_guard))
        self.commands = self.enterContext(patch.object(setup, "cmd", side_effect=self.command))
        self.log = []

    def clear_guard(self):
        self.guard = False

    def command(self, *args, **kwargs):
        self.log.append(args)
        if args[:2] != ("systemctl", "--user"):
            return
        action = args[2]
        if action in ("enable", "disable"):
            self.current["enabled"] = action == "enable"
        if action in ("start", "stop") or "--now" in args:
            self.current["active"] = action in ("enable", "start")
            self.guard = self.current["active"]

    def files(self, managed=False, complete=True):
        data = setup.load_manifest()
        # The original published release did not yet ship haptics or suggestions.
        for name in ("bridge.py", "chord.py", "profile.py", "keyboard.py", "DeckSelector.qml"):
            path = setup.APP / name
            path.write_text("KEYBOARD = 0x90000002\n" if name == "profile.py" else "fixture\n")
        (setup.APP / "keyboard.py").chmod(0o755)
        setup.UNIT.write_text("[Service]\nExecStart=/usr/bin/python3 \""+str(setup.APP/"bridge.py")+
                              "\"\nExecStopPost=/usr/bin/python3 \""+str(setup.APP/"profile.py")+"\" leave\n")
        setup.ENTRY.write_text('[Desktop Entry]\nExec="'+str(setup.APP/"keyboard.py")+
                               '"\nX-KDE-Wayland-VirtualKeyboard=true\n')
        setup.TEMPLATE.write_text("controller layout\n")
        if managed:
            owned = [setup.UNIT, setup.ENTRY, setup.TEMPLATE, *setup.APP.iterdir()]
            data["files"] = {str(path): setup.digest(path.read_bytes()) for path in owned}
            data["enabled"] = complete
            if complete:
                data["previous_method"] = "prior.desktop"
            setup.record(data)
        return data

    def test_manual_round_trip_without_claiming_files(self):
        data = self.files()
        before = copy.deepcopy(self.current)
        originals = {p: p.read_bytes() for p in (setup.ENTRY, setup.UNIT, setup.TEMPLATE, *setup.APP.iterdir())}
        info = setup.status(data)
        self.assertEqual((info["installation"], info["mode"], info["setup_complete"], info["shortcut_appid"]),
                         ("manual", "plasma", True, 0x90000002))
        setup.switch_mode(data, "steam")
        self.assertEqual(self.current, {"method": None, "active": False, "enabled": False,
                                        "policy": {"raw": "0", "mode": 0}})
        self.assertFalse(self.guard)
        self.assertIn(("/usr/bin/python3", str(setup.APP/"profile.py"), "leave"), self.log)
        self.assertEqual(setup.status(data)["mode"], "steam")
        self.log.clear()
        setup.switch_mode(data, "steam")
        self.assertNotIn(("systemctl", "--user", "disable", "--now", setup.UNIT_NAME), self.log)
        setup.switch_mode(data, "plasma")
        self.assertEqual(self.current, before)
        setup.switch_mode(data, "plasma")
        self.assertFalse(setup.MANIFEST.exists())
        self.assertEqual({p:p.read_bytes() for p in originals}, originals)

    def test_managed_earlier_release_and_nonadapter_method_in_mixed_state(self):
        data = self.files(managed=True)
        self.current.update(method="user-selected.desktop", policy={"raw": "2", "mode": 2})
        self.assertEqual(setup.status(data)["mode"], "mixed")
        setup.switch_mode(data, "steam")
        self.assertEqual(self.current["method"], "user-selected.desktop")
        setup.switch_mode(data, "plasma")
        self.assertEqual(self.current["policy"], {"raw": "2", "mode": 2})
        setup.switch_mode(data, "steam")
        self.assertEqual(self.current["method"], "user-selected.desktop")

    def test_plasma_from_other_input_method_remembers_that_method(self):
        data = self.files(managed=True)
        self.current["method"] = "chosen-later.desktop"
        setup.switch_mode(data, "plasma")
        self.assertEqual(self.current["method"], str(setup.ENTRY))
        setup.switch_mode(data, "steam")
        self.assertEqual(self.current["method"], "chosen-later.desktop")

    def test_incomplete_enable_does_not_mark_setup_complete(self):
        data = self.files(managed=True, complete=False)
        data["previous_method"] = "previous.desktop"
        self.assertFalse(setup.status(data)["setup_complete"])

    def test_partial_prepare_and_foreign_files(self):
        data = setup.load_manifest()
        data["files"][str(setup.APP/"bridge.py")] = "first-file"
        setup.record(data)
        info = setup.status(data)
        self.assertEqual((info["installation"], info["prepared"], info["can_switch"]), ("managed", False, False))
        setup.MANIFEST.unlink()
        self.files()
        setup.UNIT.write_text("[Service]\nExecStart=/usr/bin/true\n")
        with self.assertRaisesRegex(RuntimeError, "another installation"):
            setup.switch_mode(data, "steam")
        self.assertFalse(self.log)

    def test_fresh_prepare_preserves_desktop_until_requested_and_removal_restores_disabled_policy(self):
        data = self.files(managed=True, complete=False)
        original = {"active": False, "enabled": False, "method": "other.desktop",
                    "policy": {"raw": "0", "mode": 0}}
        self.current.update(copy.deepcopy(original))
        self.assertTrue(setup.status(data)["prepared"])
        self.assertFalse(setup.status(data)["setup_complete"])
        setup.switch_mode(data, "steam")
        self.assertEqual(self.current, original)
        self.assertFalse(self.log)
        self.assertFalse(setup.mode_file().exists())

        def enable_first(state):
            state["previous_method"] = self.current["method"]
            state["enabled"] = True
            setup.record(state)
            self.current.update(method=str(setup.ENTRY), active=True, enabled=True)
        with patch.object(setup, "enable", side_effect=enable_first):
            setup.switch_mode(data, "plasma")
        self.assertEqual(self.current["policy"], {"raw": None, "mode": 1})
        setup.remove(data)
        self.assertEqual(self.current, original)
        self.assertFalse(setup.mode_file().exists())

    def test_old_install_with_only_leftover_directories_can_be_reinstalled(self):
        (setup.APP / "words").mkdir()
        cache = setup.APP / "__pycache__"
        cache.mkdir()
        (cache / "profile.cpython-test.pyc").write_bytes(b"unused")
        self.assertEqual(setup.status(setup.load_manifest())["installation"], "missing")

    def test_failed_switch_rolls_back_and_can_retry(self):
        data = self.files()
        original = copy.deepcopy(self.current)
        set_real = self.set_method.side_effect
        def failure(value):
            if value is None:
                raise RuntimeError("simulated config write failure")
            set_real(value)
        self.set_method.side_effect = failure
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            setup.switch_mode(data, "steam")
        self.assertEqual(self.current, original)
        self.assertFalse(setup.mode_file().exists())
        self.set_method.side_effect = set_real
        setup.switch_mode(data, "steam")
        self.assertEqual(setup.status(data)["mode"], "steam")

    def test_interrupted_switch_recovers_recorded_snapshot(self):
        data = self.files()
        original = copy.deepcopy(self.current)
        setup.store_mode({"pending": original})
        self.current.update(active=False, enabled=False)
        self.assertTrue(setup.status(data)["recovery_pending"])
        setup.switch_mode(data, "plasma")
        self.assertEqual(self.current, original)
        self.assertFalse(setup.mode_file().exists())

    def test_policy_restores_implicit_key_after_kwin_persists_typed_mode(self):
        config = {"raw": "0", "mode": 0}
        panel = Mock()
        def set_runtime(_iface, _name, value, **kw):
            config.update(raw=str(int(value)), mode=int(value))
        panel.Set.side_effect = set_runtime
        with patch.object(setup,"panel_state",side_effect=lambda: config.copy()), \
             patch.object(setup,"panel_properties",return_value=panel), \
             patch.object(setup,"kwin_setting",side_effect=lambda _: config["raw"]), \
             patch.object(setup,"set_kwin_setting",side_effect=lambda _, raw: config.update(raw=raw)):
            # Call the real setter despite the isolated mock used in the other tests.
            REAL_POLICY({"raw": None, "mode": 1})
        self.assertEqual(config, {"raw": None, "mode": 1})


REAL_POLICY = setup.set_policy

if __name__ == "__main__":
    unittest.main()
