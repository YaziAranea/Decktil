"""Check the Deck haptic wire format and session behavior without a controller."""
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import haptics


class TrackpadHaptics(unittest.TestCase):
    def test_device_commands(self):
        feedback = haptics.TrackpadFeedback()
        with patch.object(haptics.fcntl, "ioctl", return_value=65) as ioctl:
            for mask, wire_side in ((1, 1), (2, 0), (3, 2)):
                with self.subTest(mask=mask):
                    self.assertEqual(feedback.send(42, mask, feedback.preset), 65)
                    fd, command, report, mutate = ioctl.call_args.args
                    self.assertEqual((fd, command, mutate), (42, haptics.HIDIOCSFEATURE_65, True))
                    self.assertEqual(len(report), 65)
                    self.assertEqual(report[:4], bytes((0, 0x8F, 8, wire_side)))
                    self.assertEqual(struct.unpack_from("<HHHb", report, 4), feedback.preset)
                    self.assertEqual(report[11:], bytes(54))

    def test_commit_and_navigation(self):
        feedback = haptics.TrackpadFeedback()
        with patch.object(feedback, "send") as send, patch.object(haptics.time, "monotonic") as now:
            now.return_value = 1.0
            feedback.play(42, 1, 3)
            self.assertEqual(send.call_args_list,
                             [call(42, 1, feedback.preset), call(42, 2, haptics.TICK)])
            now.return_value = 1.01
            feedback.play(42, 0, 3)
            self.assertEqual(send.call_count, 2)
            now.return_value = 1.05
            feedback.play(42, 0, 3)
            send.assert_called_with(42, 2, haptics.TICK)
            now.return_value = 1.13
            feedback.play(42, 0, 3)
            send.assert_called_with(42, 3, haptics.TICK)
            feedback.play(42, 3, 0)
            send.assert_called_with(42, 3, feedback.preset)

    def test_off_quiet_custom_and_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            userdata = Path(directory)
            path = userdata / "1/config/localconfig.vdf"
            path.parent.mkdir(parents=True)
            path.write_text('"keyboard_haptic_preset" "0"\n')
            feedback = haptics.TrackpadFeedback()
            feedback.preset = haptics.steam_keyboard_preset(userdata)
            with patch.object(feedback, "send") as send:
                feedback.play(42, 1, 2)
                send.assert_not_called()

            custom = json.dumps({"eHapticType": 2, "unIntensity": 100, "ndBGain": -100})
            path.write_text('"keyboard_haptic_preset" "10"\n"keyboard_haptic_custom" '
                            + json.dumps(custom) + "\n")
            feedback.preset = haptics.steam_keyboard_preset(userdata)
            self.assertEqual(feedback.preset, haptics.PRESETS[2]._replace(gain_db=-24))
            with patch.object(feedback, "send") as send:
                feedback.play(42, 1, 2)
                self.assertEqual(send.call_args_list[-1].args[-1].gain_db, -24)
            path.write_text('"keyboard_haptic_preset" "invalid"')
            self.assertEqual(haptics.steam_keyboard_preset(userdata), haptics.PRESETS[2])

    def test_presets_make_distinct_short_pulses(self):
        hover = haptics.TICK
        previous_power = 0
        for effect in (hover, *[haptics.PRESETS[n] for n in (1, 2, 3)]):
            # Compare total commanded drive at the chosen gain, not perceived strength.
            drive = effect.on_us * effect.repeats * 10 ** (effect.gain_db / 20)
            self.assertGreater(drive, previous_power)
            previous_power = drive
            self.assertLess((effect.on_us + effect.off_us) * effect.repeats, 24000)

    def test_pressure_has_a_light_detent_and_independent_commit_strength(self):
        feedback = haptics.TrackpadFeedback()
        with patch.object(feedback, "send") as send:
            feedback.play(42, 0, 1, preload=1, forces=(1000, 0))
            self.assertEqual(send.call_count, 1)
            _, side, light = send.call_args.args
            self.assertEqual(side, 1)
            feedback.play(42, 3, 0, preload=3, forces=(2400, 9500))
            left, right = [entry.args for entry in send.call_args_list[1:]]
            self.assertEqual((left[1], right[1]), (1, 2))
            self.assertLess(light.repeats, left[2].repeats)
            self.assertLess(left[2].on_us, right[2].on_us)
            self.assertLess(left[2].repeats, right[2].repeats)
            self.assertEqual(left[2].gain_db, right[2].gain_db)
            self.assertLess((right[2].on_us + right[2].off_us) * right[2].repeats, 24000)
            feedback.play(42, 1, 0, forces=(2400, 0))
            self.assertEqual(send.call_args.args, left)

    def test_pressure_edges_and_rapid_same_key_double_click(self):
        from unittest.mock import Mock
        from gi.repository import GLib
        import bridge

        adapter = object.__new__(bridge.Bridge)
        adapter.chord = Mock(stage=None, armed=False, client_ui=False, dismiss_b_held=False)
        adapter.chord.update.return_value = False
        adapter.erase_serial = None
        adapter.Frame = Mock()
        adapter.feedback = haptics.TrackpadFeedback()
        adapter.feedback.send = Mock()
        adapter.fd = 42
        adapter.ready = adapter.visible = True
        adapter.locked = adapter.in_game = False
        adapter.desktop_erase_until = 0.0
        adapter.last_frame = adapter.last_down = None
        adapter.held_triggers = adapter.preloaded = adapter.frame_serial = 0
        adapter.last_send = 0
        adapter.recent_frames = {}
        adapter.force_samples = [None, None]
        adapter.click_timers = set()
        rd, wr = os.pipe2(os.O_NONBLOCK)
        def report(force, *, pressed=False, touch=True):
            data = bytearray(64)
            data[:4] = bytes((1, 0, 9, 64))
            data[10] = int(touch) << 3 | int(pressed) << 1
            struct.pack_into("<H", data, 56, force)
            os.write(wr, data)
            adapter.read_reports(rd, GLib.IO_IN)
        try:
            # Keep all 13 samples within a single visual frame.
            with patch("time.monotonic", return_value=1.0):
                report(0)
                report(1100); report(1100); report(900)
                report(2400, pressed=True); report(3000, pressed=True)
                report(200); report(900); report(1100)
                report(2400, pressed=True); report(9500, pressed=True); report(300, pressed=False)
                report(0, pressed=True, touch=False); report(0)
                frames = [row.args for row in adapter.Frame.call_args_list]
                self.assertEqual([row[5] for row in frames if row[5]], [4, 1, 4, 1])
                self.assertEqual([row[6] for row in frames], [0, 0, 1, 0, 0, 1, 0, 0, 0])
                for row in frames:
                    if row[5]:
                        adapter.Feedback(row[9], row[5] & 3, row[5] & 12)
                        adapter.Feedback(row[9], row[5] & 3, row[5] & 12)
                pulses = [r.args[2] for r in adapter.feedback.send.call_args_list]
                self.assertEqual(len(pulses), 4)
                self.assertEqual(pulses[0], pulses[2])
                self.assertLess(pulses[1].repeats, pulses[3].repeats)
            with patch("time.monotonic") as clock, patch.object(GLib, "timeout_add", return_value=19) as delay:
                clock.return_value = 2.0
                report(2400, pressed=True)
                serial = adapter.Frame.call_args.args[9]
                adapter.Feedback(serial, 1, 0)
                self.assertEqual(adapter.feedback.send.call_count, 4)
                milliseconds, callback = delay.call_args.args
                self.assertEqual(milliseconds, 8)
                clock.return_value = 2.004
                report(9500, pressed=True)
                clock.return_value = 2.008
                callback()
                self.assertEqual(adapter.feedback.send.call_args.args[2], pulses[3])
                self.assertFalse(adapter.click_timers)
        finally:
            os.close(wr)
            os.close(rd)

    def test_write_failure_suppresses_only_current_session(self):
        feedback = haptics.TrackpadFeedback()
        with patch.object(haptics.fcntl, "ioctl", return_value=0) as ioctl:
            with self.assertLogs(haptics.log, "WARNING"):
                feedback.play(42, 1, 0)
            feedback.play(42, 2, 0)
            ioctl.assert_called_once()
        with patch.object(haptics, "steam_keyboard_preset", return_value=haptics.PRESETS[1]):
            feedback.begin()
        self.assertFalse(feedback.unavailable)
        self.assertEqual(feedback.preset, haptics.PRESETS[1])


if __name__ == "__main__":
    unittest.main()
