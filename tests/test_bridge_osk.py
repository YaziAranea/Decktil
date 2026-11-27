"""Exercise live controller routing while Steam temporarily owns its OSK profile."""
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from gi.repository import GLib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import bridge


class DesktopOskHandoff(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        pin = {"pid": 42, "index": 7}
        state = root / "pin"
        state.write_text(json.dumps(pin))
        template = root / "template.vdf"
        self.enterContext(patch.multiple(bridge.mapping, STATE=state, TEMPLATE=template,
                                         KEYBOARD=0x80001234))
        self.enterContext(patch.object(bridge.mapping, "valid", return_value=False))
        self.enterContext(patch.object(bridge.mapping, "steam_pid", return_value=42))
        self.game = self.enterContext(patch.object(bridge.mapping, "running_game", return_value=False))
        self.app = self.enterContext(patch.object(bridge.mapping, "active_app", return_value=769))
        self.enterContext(patch.object(bridge.mapping, "selected_path", return_value=template))
        self.displaced = self.enterContext(patch.object(bridge.mapping, "osk_displaced", return_value=True))
        self.clock = self.enterContext(patch("time.monotonic", return_value=10.45))
        a = self.adapter = object.__new__(bridge.Bridge)
        a.ready = a.visible = True
        a.locked = a.in_game = False
        a.chord = Mock(last_desktop_osk=10, stage=None, armed=False, stock_open=False, echo_until=0,
                       client_ui=False, dismiss_b_held=False)
        a.chord.update.return_value = False
        a.chord._state.return_value = dict(visible=True, available=True, active=True, mode=1,
                                          activeClientSupportsTextInput=True)
        a.fd = 101
        a.steam_pid = 42
        a.verified_pin = pin.copy()
        a.route_until = 0
        a.desktop_erase_until = 0.0
        a.last_frame = a.last_down = None
        a.erase_serial = None
        a.erase_release_deadline = 0.0
        a.frame_serial = a.held_triggers = a.preloaded = a.last_send = 0
        a.field_return = Mock()
        a.Frame = Mock()
        a.CancelRepeat = Mock()
        a.PersonalChanged = Mock()
        a.feedback = Mock()
        a.force_samples = [None, None]
        a.click_timers = set()
        a.recent_frames = {}

    def test_live_edge_and_ack_during_recovery_are_not_replayed(self):
        a = self.adapter
        a.validate_profile()
        self.assertFalse(a.ready)  # The background pin recovery must still run.
        self.assertTrue(a.can_route())
        rd, wr = os.pipe2(os.O_NONBLOCK)
        self.addCleanup(os.close, rd)
        self.addCleanup(os.close, wr)
        for trigger in (False, True, True, False):
            data = bytearray(64)
            data[:4] = bytes((1, 0, 9, 64))
            data[10] = 2 << 3
            struct.pack_into("<HH", data, 44, 0, 12000 if trigger else 0)
            os.write(wr, data)
            a.read_reports(rd, GLib.IO_IN)
        presses = [call.args for call in a.Frame.call_args_list if call.args[5] & 3]
        self.assertEqual([row[5] for row in presses], [2])
        serial = presses[0][9]
        a.Feedback(serial, 2, 0)
        a.Feedback(serial, 2, 0)
        a.feedback.play.assert_called_once_with(101, 2, 0, 0, [0, 0])
        self.clock.return_value = 11.3
        self.assertFalse(a.can_route())

    def test_requires_same_pin_same_foreground_and_recent_stock_request(self):
        a = self.adapter
        pin = a.verified_pin
        scenarios = [("no prior pin", None, 769, False, 10, True, True),
                     ("different index", {"pid": 42, "index": 8}, 769, False, 10, True, True),
                     ("game", pin, 769, True, 10, True, True),
                     ("other active profile", pin, 443510, False, 10, True, True),
                     ("expired", pin, 769, False, 8, True, True),
                     ("unrelated Steam UI", pin, 769, False, 10, False, True),
                     ("no Wayland editor", pin, 769, False, 10, True, False)]
        for name, owned, app, game, stamp, stock, editor in scenarios:
            with self.subTest(name=name):
                a.ready, a.verified_pin = True, owned
                self.app.return_value, self.game.return_value = app, game
                self.displaced.return_value = stock
                a.chord.last_desktop_osk = stamp
                a.chord._state.return_value["activeClientSupportsTextInput"] = editor
                a.validate_profile()
                self.assertFalse(a.can_route())

    def test_editor_deactivation_ends_handoff(self):
        a = self.adapter
        a.validate_profile()
        a.chord._state.return_value["active"] = False
        a.editor_changed()
        self.assertFalse(a.can_route())
        self.assertIsNone(a.verified_pin)
        a.PersonalChanged.assert_called_once_with()

    def physical_reports(self):
        a = self.adapter
        # Run the actual shortcut classifier and report decoder together.
        state = a.chord._state.return_value
        with patch.dict(os.environ, XDG_SESSION_TYPE="x11"), patch.object(bridge.dbus, "Interface"):
            a.chord = bridge.SteamChord(Mock())
        a.chord.enabled = True
        a.chord.poll_desktop_actions = Mock()
        a.chord._state = lambda: state
        a.chord._desktop_x_enabled = Mock(return_value=True)
        a.chord._request = Mock()
        rd, wr = os.pipe2(os.O_NONBLOCK)
        self.addCleanup(os.close, rd)
        self.addCleanup(os.close, wr)
        def report(x=False, *, b=False, guide=False, pad=False):
            data = bytearray(64)
            data[:4] = bytes((1, 0, 9, 64))
            data[8] = (0x40 if x else 0) | (0x20 if b else 0)
            data[9] = 0x20 if guide else 0
            data[10] = 0x0a if pad else 0
            os.write(wr, data)
            a.read_reports(rd, GLib.IO_IN)
        return report

    def test_big_picture_closes_pending_plasma_and_leaves_native_pads(self):
        a = self.adapter
        a.game_running = False
        a.panel_epoch = 0
        a.popup_guard = Mock()
        a.sync_profile = Mock()
        a.props = Mock()
        a.props.GetAll.return_value = dict(visible=True, available=True, mode=1)
        a.chord.client_ui = True
        a.poll_desktop_actions()
        a.chord._hide_panel.assert_called_once()
        a.popup_guard.set_enabled.assert_called_with(False)
        a.chord.reconcile.assert_not_called()
        self.assertTrue(a.in_game)
        self.assertFalse(a.can_route())
        a.visibility()  # A late visibleChanged from the prior desktop activation.
        self.assertEqual(a.chord._hide_panel.call_count, 2)
        a.chord.client_ui = False
        a.props.GetAll.return_value["visible"] = False
        a.poll_desktop_actions()
        self.assertFalse(a.in_game)
        a.popup_guard.set_enabled.assert_called_with(True)

    def test_physical_erase_is_single_edge_released_without_touch(self):
        a = self.adapter
        report = self.physical_reports()
        # A first report with X already down has no editing edge.
        report(True)
        report()
        for x in (True, True, False, True, False):
            report(x)
        frames = [call.args for call in a.Frame.call_args_list]
        bit = bridge.ERASE_BUTTON
        self.assertEqual([(row[5], row[6]) for row in frames],
                         [(0, 0), (bit, bit), (0, 0), (bit, bit), (0, 0)])
        a.CloseOnEmpty(frames[1][9])  # First tap has been superseded by the second.
        a.chord._request.assert_not_called()
        a.CloseOnEmpty(frames[3][9])
        a.chord._request.assert_called_once_with(False)
        a.CloseOnEmpty(frames[3][9])
        a.chord._request.assert_called_once_with(False)
        a.Frame.reset_mock()
        report(True, guide=True); report(False)
        self.assertEqual(a.chord._request.call_args.args, (False,))
        a.chord._desktop_x_enabled.return_value = False
        report(True); report(False)
        self.assertFalse(any(call.args[5] for call in a.Frame.call_args_list))

    def test_b_stops_held_x_and_releases_mouse_without_editing_again(self):
        a = self.adapter
        report = self.physical_reports()
        a.sync_profile = Mock()
        report()
        report(True)
        serial = a.erase_serial
        a.Frame.reset_mock()
        report(True, b=True, pad=True)
        a.chord._request.assert_called_once_with(False)
        a.CancelRepeat.assert_called_once_with()
        a.field_return.arm.assert_called_once_with()
        a.Frame.assert_called_once_with(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        a.CloseOnEmpty(serial)
        self.assertIsNone(a.erase_serial)
        a.visible = False
        report(True, b=True)
        a.sync_profile.assert_not_called()
        report(True)
        a.sync_profile.assert_called_once_with()
        report(True)
        a.chord._request.assert_called_once_with(False)

    def test_hidden_profile_releases_after_b_up_or_loss_of_reports(self):
        a = self.adapter
        report = self.physical_reports()
        a.job = None
        a.panel_epoch = 1
        start = self.enterContext(patch.object(bridge.subprocess, "Popen"))
        self.enterContext(patch.object(bridge.GLib, "timeout_add"))
        report()
        report(b=True)
        a.visible = False
        a.sync_profile()
        start.assert_not_called()
        report(b=True)
        report()
        self.assertEqual(start.call_args.args[0][-1], "leave")
        start.reset_mock()
        a.job = None
        a.visible = True
        report(b=True)
        a.visible = False
        self.clock.return_value += 1
        a.sync_profile()
        self.assertEqual(start.call_args.args[0][-1], "leave")

    def test_pointer_reentry_rechecks_hidden_desktop_and_support_for_text(self):
        a = self.adapter
        a.visible = False
        a.chord.b = False
        state = a.chord._state.return_value
        state.update(visible=False, active=False)
        self.assertTrue(a.reopen_browser_field())
        a.chord._request.assert_called_once_with(True)
        a.chord._request.reset_mock()
        state["activeClientSupportsTextInput"] = False
        self.assertFalse(a.reopen_browser_field())
        state["activeClientSupportsTextInput"] = True
        for obj, flag in [(a, "visible"), (a, "locked"), (a, "in_game"),
                          (a.chord, "b"), (a.chord, "client_ui")]:
            with patch.object(obj, flag, True):
                self.assertFalse(a.reopen_browser_field())
        a.chord._request.assert_not_called()

    def test_b_needs_a_new_bare_edge_on_a_visible_desktop_panel(self):
        a = self.adapter
        report = self.physical_reports()
        report(b=True)
        report()
        report(b=True, guide=True)
        report(b=True)
        a.visible = False
        report()
        report(b=True)
        a.visible = True
        report(b=True)
        report()
        a.chord._request.assert_not_called()
        report(b=True)
        a.chord._request.assert_called_once_with(False)

    def test_modifier_and_profile_changes_cannot_rearm_a_held_press(self):
        a = self.adapter
        report = self.physical_reports()
        report()
        report(True)
        serial = a.erase_serial
        self.assertIsNotNone(serial)
        a.last_down = a.last_frame = None  # New helper pin starts a pad baseline.
        report(True)
        self.assertEqual(a.Frame.call_args.args[5:7], (0, bridge.ERASE_BUTTON))
        report(True, guide=True)
        report(True)
        self.assertEqual(a.Frame.call_args.args[5:7], (0, 0))
        a.CloseOnEmpty(serial)
        a.chord._request.assert_not_called()
        report()
        report(True)
        a.last_down = a.last_frame = None
        report()
        self.assertAlmostEqual(a.erase_release_deadline, self.clock.return_value + .4)

    def test_fresh_x_during_verified_desktop_acquisition_does_not_bind_pads(self):
        a = self.adapter
        report = self.physical_reports()
        a.ready = False
        a.job = Mock()
        self.enterContext(patch.object(bridge.mapping, "desktop_source", return_value=(7, Path("vdf"))))
        self.app.return_value = bridge.mapping.DESKTOP
        report()
        report(True, pad=True)
        serial = a.erase_serial
        self.assertIsNotNone(serial)
        self.assertEqual(a.Frame.call_args.args[4:7], (0, bridge.ERASE_BUTTON, bridge.ERASE_BUTTON))
        report(False, pad=True)
        self.assertEqual(a.Frame.call_args.args[4:7], (0, 0, 0))
        a.CloseOnEmpty(serial)
        a.chord._request.assert_called_once_with(False)
        self.app.return_value = bridge.mapping.STEAM_UI
        a.Frame.reset_mock()
        report(True, pad=True)
        a.Frame.assert_not_called()
        self.assertIsNone(a.erase_serial)

    def test_accessibility_confirmation_expires_with_erase_or_panel(self):
        a = self.adapter
        a.panel_epoch = 2
        a.focus_window = ("win-a", 27, True)
        a.popup_guard = Mock(active=True)
        a.popup_guard.bus.get_name_owner.return_value = ":1.10"
        a.erase_serial = 17
        a.erase_release_deadline = float("inf")
        reply = Mock()
        with patch.object(bridge, "check_empty_async") as lookup:
            a.ConfirmEmpty(17, True, reply, Mock())
            self.assertEqual(lookup.call_args.args[:2], (27, True))
            finish = lookup.call_args.args[2]
            finish(True)
            reply.assert_called_once_with(True)
            a.panel_epoch += 1
            finish(True)
            reply.assert_called_with(False)
            a.panel_epoch -= 1
            a.erase_serial += 1
            finish(True)
            reply.assert_called_with(False)
            a.ConfirmEmpty(17, True, reply, Mock())
            lookup.assert_called_once()

    def test_window_change_authenticates_kwin_and_invalidates_old_press(self):
        a = self.adapter
        a.focus_window = ("one", 32, True)
        a.erase_serial = 18
        a.popup_guard = Mock()
        a.popup_guard.bus.get_name_owner.return_value = ":1.10"
        a.FocusWindow("two", 93, False, sender=":1.0")
        self.assertEqual(a.focus_window, ("one", 32, True))
        self.assertEqual(a.erase_serial, 18)
        a.FocusWindow("two", 32, False, sender=":1.10")
        self.assertEqual(a.focus_window, ("two", 32, False))
        self.assertIsNone(a.erase_serial)

    def test_empty_close_stays_with_current_editor_and_recent_x(self):
        a = self.adapter
        a.erase_serial = 17
        a.erase_release_deadline = 10.5
        self.clock.return_value = 10.6
        a.CloseOnEmpty(17)  # Delayed reply after release.
        a.chord._request.assert_not_called()
        a.erase_release_deadline = float("inf")
        a.chord._state.return_value["activeClientSupportsTextInput"] = False
        a.editor_changed()
        a.CancelRepeat.assert_called_once_with()
        a.chord._state.return_value["activeClientSupportsTextInput"] = True
        a.CloseOnEmpty(17)  # A different editor acquired focus.
        a.chord._request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
