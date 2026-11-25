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
        a.chord = Mock(last_desktop_osk=10, stage=None, armed=False, stock_open=False, echo_until=0)
        a.chord._state.return_value = dict(visible=True, available=True, active=True, mode=1,
                                          activeClientSupportsTextInput=True)
        a.fd = 101
        a.steam_pid = 42
        a.verified_pin = pin.copy()
        a.route_until = 0
        a.last_frame = a.last_down = a.physical_b = None
        a.erase_serial = None
        a.erase_release_deadline = 0.0
        a.frame_serial = a.held_triggers = a.preloaded = a.last_send = 0
        a.Frame = Mock()
        a.CancelRepeat = Mock()
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

    def test_physical_erase_is_single_edge_released_without_touch(self):
        a = self.adapter
        a.chord.desktop_b_closes_keyboard.return_value = True
        rd, wr = os.pipe2(os.O_NONBLOCK)
        self.addCleanup(os.close, rd)
        self.addCleanup(os.close, wr)
        def report(b, *, guide=False):
            data = bytearray(64)
            data[:4] = bytes((1, 0, 9, 64))
            data[8] = 0x20 if b else 0
            data[9] = 0x20 if guide else 0
            os.write(wr, data)
            a.read_reports(rd, GLib.IO_IN)

        for b in (False, True, True, False, True, False):
            report(b)
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
        a.chord.desktop_b_closes_keyboard.return_value = False
        report(True); report(False)
        self.assertFalse(any(call.args[5] for call in a.Frame.call_args_list))

    def test_empty_close_stays_with_current_editor_and_recent_b(self):
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
