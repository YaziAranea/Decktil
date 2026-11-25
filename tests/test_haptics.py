"""Check the Deck haptic wire format and session behavior without a controller."""
import json
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
            self.assertLess((effect.on_us + effect.off_us) * effect.repeats, 8000)

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
