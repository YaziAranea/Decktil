"""Verify the manager's cross-session transaction without contacting a desktop or user bus."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import setup


class AllModes(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.units = self.root / "config/systemd/user"
        state = self.root / "state"
        state.mkdir()
        self.enterContext(patch.multiple(setup, STATE_DIR=state, MANIFEST=state / "install.json",
                                         UNIT=self.units / setup.UNIT_NAME, APP=self.root / "app"))
        self.fullscreen = setup.fullscreen_installer()
        self.enterContext(patch.object(setup, "fullscreen_installer", return_value=self.fullscreen))
        self.enterContext(patch.object(self.fullscreen, "base_directory", return_value=self.root / "game"))
        self.enterContext(patch.object(setup, "desktop_session", return_value=True))
        self.enterContext(patch.object(setup, "installation", return_value=("manual", True, 0x90000001)))
        self.desktop = {"active": False, "enabled": False, "method": "original",
                        "policy": {"mode": 0, "raw": "0"}}
        self.enterContext(patch.object(setup, "snapshot", side_effect=lambda: copy.deepcopy(self.desktop)))
        self.enterContext(patch.object(setup, "restore_snapshot", side_effect=self.desktop.update))
        self.enterContext(patch.object(setup, "switch_mode", side_effect=self.switch))
        self.enterContext(patch.object(self.fullscreen, "prepare_release", side_effect=self.release))
        self.flags = {name: {"active": False, "enabled": False} for name in self.fullscreen.UNITS}
        self.enterContext(patch.object(self.fullscreen, "systemctl", side_effect=self.systemctl))
        self.enterContext(patch.object(setup, "manual_runtime_updates", return_value={}))
        self.fail_restart = False

    def release(self):
        target = "releases/" + "a" * 64
        dest = self.fullscreen.base_directory() / target / "experimental/gaming"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "session.py").write_text("pass\n")
        return target

    def switch(self, state, choice):
        self.desktop.update(active=choice == "plasma", enabled=choice == "plasma")
        setup.store_mode({"mode": choice})

    def systemctl(self, action, *args, **kwargs):
        if action == "show":
            flags = self.flags[args[0]]
            text = ("ActiveState=" + ("active" if flags["active"] else "inactive") +
                    "\nUnitFileState=" + ("enabled" if flags["enabled"] else "disabled") + "\n")
            return subprocess.CompletedProcess(args, 0, stdout=text)
        if action == "restart" and self.fail_restart:
            raise RuntimeError("simulated service failure")
        for name in [arg for arg in args if arg in self.flags]:
            if action in {"enable", "disable"}:
                self.flags[name]["enabled"] = action == "enable"
            if action in {"stop", "restart"} or "--now" in args:
                self.flags[name]["active"] = action == "restart"

    def test_fresh_enable_stock_and_reenable(self):
        self.assertEqual(set(self.fullscreen.status(self.units).values()), {"missing"})
        for choice in ("plasma", "steam", "plasma"):
            setup.switch_all({}, choice)
            self.assertEqual(self.desktop["active"], choice == "plasma")
            self.assertEqual(self.fullscreen.status(self.units), dict.fromkeys(
                ("gaming_mode", "big_picture"), "enabled" if choice == "plasma" else "disabled"))
            self.assertEqual(self.flags[self.fullscreen.BIG_PICTURE_UNIT]["active"], choice == "plasma")
            self.assertFalse(self.flags[self.fullscreen.UNIT]["active"])  # Started by Gamescope on login.
            self.assertFalse(setup.transaction_file().exists())

    def test_failure_rolls_back_and_retry_can_finish(self):
        self.fail_restart = True
        before = copy.deepcopy(self.desktop)
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            setup.switch_all({}, "plasma")
        self.assertEqual(self.desktop, before)
        self.assertFalse(setup.mode_file().exists())
        self.assertIsNone(self.fullscreen.current_release())
        self.assertEqual(set(self.fullscreen.status(self.units).values()), {"missing"})
        self.fail_restart = False
        setup.switch_all({}, "plasma")
        self.assertEqual(self.fullscreen.status(self.units)["gaming_mode"], "enabled")

    def test_recovery_after_an_interrupted_first_enable(self):
        before = {"desktop": copy.deepcopy(self.desktop), "fullscreen": self.fullscreen.capture(self.units),
                  "mode": {}, "manifest": None, "changes": [], "fullscreen_touched": True}
        setup.transaction_file().write_text(json.dumps(before))
        self.fullscreen.install_files(self.units, self.release())
        self.fullscreen.activate()
        self.switch({}, "plasma")
        setup.switch_all({}, "steam")
        self.assertEqual(set(self.fullscreen.status(self.units).values()), {"missing"})
        self.assertEqual(self.desktop, before["desktop"])
        self.assertFalse(setup.transaction_file().exists())

    def test_standalone_legacy_game_install_can_return_to_stock(self):
        self.fullscreen.install_files(self.units, self.release())
        self.fullscreen.activate()
        with patch.object(setup, "installation", return_value=("missing", False, None)), \
                patch.object(setup, "switch_mode") as desktop:
            setup.switch_all({}, "steam")
        desktop.assert_not_called()
        self.assertEqual(set(self.fullscreen.status(self.units).values()), {"disabled"})

    def test_foreign_service_survives_with_desktop_untouched(self):
        self.units.mkdir(parents=True)
        foreign = self.units / self.fullscreen.BIG_PICTURE_UNIT
        foreign.write_text("[Service]\nExecStart=/usr/bin/true\n")
        self.assertEqual(self.fullscreen.status(self.units)["big_picture"], "foreign")
        with self.assertRaisesRegex(RuntimeError, "unmanaged"):
            setup.switch_all({}, "plasma")
        self.assertFalse(self.desktop["active"])
        self.assertIn("true", foreign.read_text())

    def test_failed_first_enable_preserves_retryable_helper_and_runtime(self):
        package = self.root / "package"
        (package / "src").mkdir(parents=True)
        (package / "src/bridge.py").write_bytes(b"new bridge")
        (package / "src/new.qml").write_bytes(b"new component")
        setup.APP.mkdir()
        bridge = setup.APP / "bridge.py"
        bridge.write_bytes(b"previous bridge")
        original = {"files": {str(bridge): setup.digest(bridge.read_bytes())}, "enabled": False}
        setup.record(original)

        def first_enable(state, choice):
            setup.managed_file(state, setup.APP / "steam-shortcut.json", b'{"appid":3000000000}', 0o600)
            state["enabled"] = True
            setup.record(state)
            self.switch(state, choice)

        with patch.multiple(setup, PACKAGE=package, RUNTIME_FILES=("bridge.py", "new.qml")), \
                patch.object(setup, "installation", return_value=("managed", True, None)), \
                patch.object(setup, "switch_mode", side_effect=first_enable):
            self.fail_restart = True
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                setup.switch_all(setup.load_manifest(), "plasma")
            self.assertEqual(setup.load_manifest(), original)
            self.assertEqual(bridge.read_bytes(), b"previous bridge")
            self.assertFalse((setup.APP / "steam-shortcut.json").exists())
            self.assertFalse((setup.APP / "new.qml").exists())
            self.fail_restart = False
            setup.switch_all(setup.load_manifest(), "plasma")
            self.assertEqual(bridge.read_bytes(), b"new bridge")
            self.assertTrue(setup.load_manifest()["enabled"])

    def test_managed_runtime_conflict_does_not_overwrite_user_changes(self):
        setup.APP.mkdir()
        file = setup.APP / "bridge.py"
        file.write_text("my local edit\n")
        with self.assertRaisesRegex(RuntimeError, "modified"):
            setup.runtime_changes({"files": {str(file): setup.digest(b"old shipped code")}})
        self.assertEqual(file.read_text(), "my local edit\n")


class QtPackageMetadata(unittest.TestCase):
    def setUp(self):
        self.installer = setup.fullscreen_installer()

    def test_available_host_database_uses_no_network(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(self.installer, "package_rows", return_value=["host package"]) as query, \
                patch.object(subprocess, "check_output") as config:
            self.assertEqual(self.installer.package_metadata(Path(folder)), ["host package"])
            query.assert_called_once_with()
            config.assert_not_called()

    def test_empty_host_database_uses_configured_steamos_branch_only(self):
        mirror = "https://steamdeck-packages.steamos.cloud/archlinux-mirror/extra-3.9/os/x86_64"
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(self.installer, "package_rows", side_effect=[[], ["resolved"]]) as query, \
                patch.object(subprocess, "check_output", side_effect=[
                    "local-custom\ncore-3.9\nextra-3.9\n", "https://extra.example/mirror\n",
                    "http://steamdeck-packages.steamos.cloud/extra\n" + mirror + "\n"]), \
                patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as fetch:
            root = Path(folder)
            self.assertEqual(self.installer.package_metadata(root), ["resolved"])
            query.assert_called_with("--config", str(root / "pacman.conf"), "--dbpath", str(root / "pacman-db"))
            fetch.assert_called_once()
            args = fetch.call_args.args[0]
            self.assertEqual(args[-1], mirror + "/extra-3.9.db")
            self.assertEqual(Path(args[args.index("--output") + 1]), root / "pacman-db/sync/extra-3.9.db")
            self.assertNotIn("--location", args)
            config = (root / "pacman.conf").read_text()
            self.assertIn("[extra-3.9]", config)
            self.assertNotIn("custom", config)
            self.assertNotIn("[core", config)

    def test_mirror_identity_rejects_spoofed_or_insecure_hosts(self):
        for server in ("http://steamdeck-packages.steamos.cloud/repo", "https://mirror.test/repo",
                       "https://steamdeck-packages.steamos.cloud.attacker.test/repo",
                       "https://steamdeck-packages.steamos.cloud@attacker.test/repo",
                       "https://user@steamdeck-packages.steamos.cloud/repo"):
            with self.subTest(server=server):
                self.assertFalse(self.installer.steamos_mirror(server))


if __name__ == "__main__":
    unittest.main()
