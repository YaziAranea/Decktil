import contextlib
import unittest
from unittest.mock import Mock, patch

from evdev import ecodes as E

import evdev_output as output


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(output, "_modmask", side_effect=lambda _, sym: {
            0xffe1: 1, 0xfe03: 128}.get(sym, 0)))
        self.enterContext(patch.object(output, "_symbol", side_effect=self.symbol))

    @staticmethod
    def symbol(display, code, state):
        if code == E.KEY_LEFTSHIFT:
            return 0xffe1, 0
        if code == E.KEY_RIGHTALT:
            return 0xfe03, 0
        if code == E.KEY_BACKSPACE:
            return 0xff08, 0
        if code != E.KEY_Q:
            return 0, 0
        if state & 128:
            return ord("€" if state & 1 else "@") | 0x01000000, 129
        upper = bool(state & 2) != bool(state & 1)
        if state >> 13 == 1:
            return (0x06ea if upper else 0x06ca), 3
        return ord("Q" if upper else "q"), 3

    def test_current_layout_case_caps_and_actions(self):
        self.assertEqual(output._strokes(None, {"й", "Й", "backspace"}, 1 << 13),
                         {"й": (E.KEY_Q,), "Й": (E.KEY_LEFTSHIFT, E.KEY_Q),
                          "backspace": (E.KEY_BACKSPACE,)})
        self.assertEqual(output._strokes(None, {"q", "Q"}, 2),
                         {"q": (E.KEY_LEFTSHIFT, E.KEY_Q), "Q": (E.KEY_Q,)})
        with self.assertRaises(output.UnmappedKey):
            output._strokes(None, {"q", "й"}, 0)

    def test_altgr_and_shift_altgr(self):
        self.assertEqual(output._strokes(None, {"@", "€"}, 0),
                         {"@": (E.KEY_RIGHTALT, E.KEY_Q),
                          "€": (E.KEY_LEFTSHIFT, E.KEY_RIGHTALT, E.KEY_Q)})

    def test_held_control_blocks_typing(self):
        def control(display, device, result):
            result._obj.base_mods = result._obj.mods = 4
            return 0
        with patch.object(output, "_get_state", side_effect=control), self.assertRaises(output.UnmappedKey):
            output._current(None)


class FocusTests(unittest.TestCase):
    def setUp(self):
        self.actual = 23
        def actual(display, window, revert):
            window._obj.value = self.actual
            return 1
        self.enterContext(patch.object(output, "_focus", side_effect=actual))
        self.enterContext(patch.object(output, "_root", return_value=2))
        self.enterContext(patch.object(output, "_connection", return_value=None))
        self.parent = self.enterContext(patch.object(output, "_parent",
            side_effect=lambda _, window: {23: 22, 22: 17, 17: 2}.get(window, 0)))

    def test_owner_and_nested_chromium_field(self):
        self.assertTrue(output._focused(None, 17))
        self.assertEqual([call.args[1] for call in self.parent.call_args_list], [23, 22])
        self.actual = 17
        self.assertTrue(output._focused(None, 17))

    def test_other_window_root_missing_window_and_pointer_focus_are_rejected(self):
        self.assertFalse(output._focused(None, 21))
        self.assertFalse(output._focused(None, 2))
        for invalid in (0, 1, 2, 98):
            self.actual = invalid
            self.assertFalse(output._focused(None, 17))


class RegistrationTests(unittest.TestCase):
    def test_no_readable_event_node_needed(self):
        self.assertIsNone(output._WriteOnlyKeyboard._find_device(None, 3))
        device = Mock(fd=3, device=None)
        def sysname(fd, command, buf):
            self.assertEqual(fd, 3)
            buf[:8] = b"input77\x00"
        with patch.object(output.fcntl, "ioctl", side_effect=sysname), \
                patch.object(output.Path, "glob", return_value=[output.Path("event17")]), \
                patch.object(output.dbus, "SessionBus") as bus:
            bus.return_value.get_object.return_value.ListKeyboards.return_value = ["event17"]
            output._wait_for_kwin(device)

    def test_registration_failure_releases_device(self):
        with patch.dict(output.os.environ, {"DISPLAY": ":fixture"}), \
                patch.object(output, "_verify_codes"), patch.object(output, "_WriteOnlyKeyboard") as device, \
                patch.object(output, "_wait_for_kwin", side_effect=OSError), self.assertRaises(OSError):
            output.EvdevOutput(17)
        device.return_value.close.assert_called_once()


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.sink = output.EvdevOutput.__new__(output.EvdevOutput)
        self.device = self.sink.device = Mock()
        self.sink.xid = 17
        self.sink.display = ":fixture"
        self.enterContext(patch.object(output, "_display", return_value=contextlib.nullcontext(None)))
        self.enterContext(patch.object(output, "_current", return_value=0))
        self.focused = self.enterContext(patch.object(output, "_focused", return_value=True))
        self.strokes = self.enterContext(patch.object(output, "_strokes",
            return_value={"q": (E.KEY_Q,), "Q": (E.KEY_LEFTSHIFT, E.KEY_Q)}))

    def test_per_key_focus_and_release(self):
        self.focused.side_effect = [True, False]
        self.assertFalse(self.sink.text("Qq", lambda: True))
        self.assertEqual([call.args for call in self.device.write.call_args_list], [
            (E.EV_KEY, E.KEY_LEFTSHIFT, 1), (E.EV_KEY, E.KEY_Q, 1),
            (E.EV_KEY, E.KEY_Q, 0), (E.EV_KEY, E.KEY_LEFTSHIFT, 0)])
        self.focused.assert_called_with(None, self.sink.xid)

    def test_unsupported_entire_string_cannot_type_a_prefix(self):
        self.strokes.side_effect = output.UnmappedKey
        with self.assertRaises(output.UnmappedKey):
            self.sink.text("qй", lambda: True)
        self.strokes.assert_called_once_with(None, {"q", "й"}, 0)
        self.device.write.assert_not_called()

    def test_modifier_release_on_failed_key_down(self):
        self.device.write.side_effect = [None, OSError("write failed"), None]
        with self.assertRaises(OSError):
            self.sink.text("Q", lambda: True)
        self.assertEqual(self.device.write.call_args.args, (E.EV_KEY, E.KEY_LEFTSHIFT, 0))

    def test_failed_key_release_destroys_device(self):
        self.device.write.side_effect = [None, OSError("disconnected")]
        with self.assertRaises(OSError):
            self.sink.text("q", lambda: True)
        self.sink.close()
        self.device.close.assert_called_once()
        self.assertFalse(self.sink.text("q", lambda: True))

    def test_non_keyboard_actions_cannot_be_sent(self):
        with self.assertRaises(output.UnmappedKey):
            self.sink.action("super", lambda: True)
        self.device.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
