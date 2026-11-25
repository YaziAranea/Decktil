"""Recover only the obsolete controller mapping left by a closed desktop OSK."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import call, patch

src = Path(__file__).resolve().parents[1] / "src/profile.py"
spec = importlib.util.spec_from_file_location("steam_profile_recovery", src)
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class ProfileRecovery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.template = self.root / "generated.vdf"
        self.template.write_text("layout")
        self.state = self.root / "state"
        self.enterContext(patch.multiple(profile, KEYBOARD=0x80001234, STATE=self.state, TEMPLATE=self.template))
        self.enterContext(patch.object(profile.time, "monotonic", return_value=10.0))
        self.enterContext(patch.object(profile.time, "strftime", return_value="2026-01-01 00:00:00"))
        self.enterContext(patch.object(profile, "steam_pid", return_value=99))
        self.enterContext(patch.object(profile, "recent_log", return_value=[]))
        self.game = self.enterContext(patch.object(profile, "running_game", return_value=False))
        self.selected = self.enterContext(patch.object(profile, "selected_path", return_value=self.template))
        self.foreground = self.enterContext(patch.object(profile.subprocess, "run", return_value=SimpleNamespace(stdout="(null)")))
        self.force = self.enterContext(patch.object(profile, "force"))
        self.enterContext(patch.object(profile, "desktop_source", return_value=(2, self.root / "desktop.vdf")))
        self.enterContext(patch.object(profile, "inherited_template", return_value="layout"))

    def log(self, origin, extra=()):
        lines = [
            f"Set OSK active 1 and appid {origin}",
            "OnFocusWindowChanged On Screen Keyboard Forcing to window type: KB ActionSet, AppID 769",
            "Loaded Config for Last Resort Path for App ID 769, Controller 2: basicui_neptune.vdf",
            "Set OSK active 0 and appid 769",
            "OnFocusWindowChanged URL Forcing to window type: k_EWindowTypeGame, 769",
            "Loaded Config for Last Resort Path for App ID 769, Controller 2: basicui_neptune.vdf",
            *extra,
        ]
        return [f"[2026-01-01 00:00:01] {line}" for line in reversed(lines)]

    def test_fresh_desktop_enter_and_reassert_existing_pin(self):
        for owned in (False, True):
            with self.subTest(owned=owned):
                if owned:
                    self.state.write_text(json.dumps({"pid":99,"index":2}))
                else:
                    self.state.unlink(missing_ok=True)
                origin = profile.KEYBOARD if owned else profile.DESKTOP
                active = [769,769,profile.KEYBOARD,profile.KEYBOARD] if owned else [769,profile.KEYBOARD,profile.KEYBOARD]
                with patch.object(profile,"recent_log",side_effect=lambda _: self.log(origin)), patch.object(profile,"active_app",side_effect=active):
                    profile.enter(9.5)
                self.force.assert_called_once_with(profile.KEYBOARD)
                self.force.reset_mock()
                self.assertEqual(json.loads(self.state.read_text()),{"pid":99,"index":2})

    def test_wrong_event_editor_or_lease_does_not_take_steam_controls(self):
        self.enterContext(patch.object(profile,"active_app",return_value=769))
        for origin,extra,when in [
            (profile.KEYBOARD,[],9.5), (42,[],9.5), (profile.DESKTOP,[],1.0),
            (profile.DESKTOP,["OnFocusWindowChanged to window type: k_EWindowTypeGamepadUI, AppID 769"],9.5),
        ]:
            with self.subTest(origin=origin,extra=extra,when=when), patch.object(profile,"recent_log",return_value=self.log(origin,extra)):
                with self.assertRaisesRegex(RuntimeError,"desktop configuration"):
                    profile.enter(when)
        self.foreground.return_value.stdout = "steam"
        with patch.object(profile,"recent_log",return_value=self.log(profile.DESKTOP)), self.assertRaisesRegex(RuntimeError,"desktop configuration"):
            profile.enter(9.8)
        self.force.assert_not_called()

    def test_releasing_a_displaced_pin_restores_automatic_desktop(self):
        self.state.write_text(json.dumps({"pid":99,"index":2}))
        with patch.object(profile,"recent_log",side_effect=lambda _: self.log(profile.KEYBOARD)), patch.object(profile,"active_app",return_value=769), patch.object(profile,"wait_for",side_effect=[False, True, True]):
            profile.release()
        self.assertEqual(self.force.call_args_list,[call(0),call(profile.DESKTOP),call(0)])
        self.assertFalse(self.state.exists())

    def test_releases_own_pin_while_guide_or_game_masks_it(self):
        for current, gaming in ((443510, False), (301, True)):
            with self.subTest(current=current):
                self.state.write_text(json.dumps({"pid":99,"index":2}))
                self.game.return_value = gaming
                event = f"OnFocusWindowChanged URL Forcing to window type: k_EWindowTypeGame, {profile.KEYBOARD}"
                with patch.object(profile,"recent_log",return_value=[event]), patch.object(profile,"active_app",return_value=current), patch.object(profile,"wait_for",return_value=False):
                    profile.release()
                self.force.assert_called_once_with(0)
                self.force.reset_mock()
                self.assertFalse(self.state.exists())

    def test_other_url_pin_takes_precedence_over_stale_active_snapshot(self):
        self.state.write_text(json.dumps({"pid":99,"index":2}))
        events = [f"OnFocusWindowChanged URL Forcing to window type: k_EWindowTypeGame, {target}" for target in (12345,profile.KEYBOARD)]
        with patch.object(profile,"recent_log",return_value=events), patch.object(profile,"active_app",return_value=profile.KEYBOARD):
            profile.release()
        self.force.assert_not_called()

    def test_hidden_helper_without_lease_restores_desktop_after_automatic_handoff(self):
        for pin in (None, 0, profile.KEYBOARD):
            with self.subTest(pin=pin), patch.object(profile,"active_app",return_value=profile.KEYBOARD), patch.object(profile,"forced_profile",return_value=pin), patch.object(profile,"wait_for",return_value=True) as wait:
                self.assertEqual(profile.orphaned_index(), 2)
                profile.release()
                self.assertEqual(self.force.call_args_list, [call(profile.DESKTOP),call(0)])
                self.assertEqual(wait.call_count,2)
            self.force.reset_mock()

    def test_clearing_pin_may_leave_the_old_mapping_active(self):
        self.state.write_text(json.dumps({"pid":99,"index":2}))
        with patch.object(profile,"active_app",return_value=profile.KEYBOARD), patch.object(profile,"forced_profile",side_effect=[profile.KEYBOARD,None]), patch.object(profile,"wait_for",side_effect=[False,True,True]):
            profile.release()
        self.assertEqual(self.force.call_args_list,[call(0),call(profile.DESKTOP),call(0)])
        self.assertFalse(self.state.exists())

    def test_orphan_check_ignores_other_contexts(self):
        for active, pin, path in ((443510,None,self.template),(profile.KEYBOARD,123,self.template),(profile.KEYBOARD,None,self.root/"other.vdf")):
            with self.subTest(active=active,pin=pin,path=path), patch.object(profile,"active_app",return_value=active),patch.object(profile,"forced_profile",return_value=pin):
                self.selected.return_value = path
                self.assertIsNone(profile.orphaned_index())
                profile.release()
        self.force.assert_not_called()

    def test_never_switch_after_another_app_or_game_has_taken_focus(self):
        self.state.write_text(json.dumps({"pid":99,"index":2}))
        self.game.return_value = True
        with patch.object(profile,"active_app",return_value=769):
            with self.assertRaisesRegex(RuntimeError,"unavailable"):
                profile.enter(9.8)
            profile.release()
        self.force.assert_not_called()


if __name__ == "__main__":
    unittest.main()
