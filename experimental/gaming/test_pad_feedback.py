import struct
import unittest
from unittest.mock import Mock, call

from controller import DeckFrames
from haptics import PRESETS
from session import Pilot
from test_controller import report
from test_session import Focus


def pad(touches, *, clicks=0, force=(0, 0), triggers=(0, 0), xy=(0, 0, 0, 0)):
    sample = bytearray(report(touch=touches, click=clicks, triggers=triggers, xy=xy))
    struct.pack_into('<HH', sample, 56, *force)
    return bytes(sample)


class PadFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.frames = DeckFrames(lambda: 42)
        self.send = self.frames.feedback.send = Mock()
        self.frames.feed(report(), 0)

    def test_pressure_moves_preview_without_vibrating_until_a_confirmed_key(self):
        touch = self.frames.feed(pad(1), .1)
        self.frames.confirm(touch[-1], 0, 3, .101)
        self.send.assert_called_once()
        self.assertEqual(self.send.call_args.args[:2], (42, 1))
        hover = self.send.call_args.args[2]
        self.assertLess(hover.on_us, PRESETS[2].on_us)
        self.assertLess(hover.repeats, PRESETS[2].repeats)
        pushed = self.frames.feed(pad(1, force=(1800, 0)), .2)
        self.assertEqual(pushed[5], 0)
        self.assertGreater(pushed[7], 0)
        self.frames.confirm(pushed[-1], 1, 12, .201)
        self.send.assert_called_once()

    def test_light_firm_and_trigger_presses_send_same_click_without_another_report(self):
        clicks = [
            pad(1, clicks=1, force=(2200, 0)),
            pad(1, clicks=1, force=(7200, 0)),
            pad(1, triggers=(12000, 0)),
            pad(2, clicks=2, force=(0, 32000)),
            pad(2, triggers=(0, 9000)),
        ]
        for index, packet in enumerate(clicks, start=1):
            now = index * .02
            sample = self.frames.feed(packet, now)
            side = 1 if index <= 3 else 2
            self.assertEqual(sample[5], side)
            self.frames.confirm(sample[-1], side, 0, now + .001)
            self.assertEqual(self.send.call_count, index)
            self.send.assert_called_with(42, side, PRESETS[2])
            self.frames.confirm(sample[-1], side, 0, now + .002)
            self.frames.feed(report(), now + .004)
        self.assertEqual(self.send.call_count, len(clicks))

    def test_pressure_only_selects_independently_and_rearms_without_lifting(self):
        for index, force in enumerate(((950, 700), (980, 700), (970, 950), (800, 960),
                                       (800, 970), (780, 650), (350, 850), (950, 800),
                                       (950, 700), (950, 800))):
            now = .1 + index * .004
            sample = self.frames.feed(pad(3, force=force), now)
            edge = {2: 1, 4: 2, 9: 1}.get(index, 0)
            self.assertEqual(sample[5] if sample else 0, edge)
            if sample:
                self.frames.confirm(sample[-1], edge, 0, now)
        self.assertEqual(self.send.call_args_list, [call(42, bit, PRESETS[2]) for bit in (1, 2, 1)])

    def test_spikes_and_moving_pressure_cannot_select_even_after_swipe_stops(self):
        packets = [(950, 0), (980, 0), (410, 0), (1100, 0), (1150, 1900),
                   (1100, 5000), (1100, 5000), (1100, 5000), (1100, 5000)]
        for index, (force, left_x) in enumerate(packets):
            sample = self.frames.feed(pad(1, force=(force, 0), xy=(left_x, 100, 0, 0)), index * .004)
            self.assertFalse(sample and sample[5])
        self.assertEqual(self.frames.down, 0)
        for force in (330, 951, 951, 951):
            sample = self.frames.feed(pad(1, force=(force, 0), xy=(5000, 100, 0, 0)), .3)
        self.assertEqual(sample[5:7], (1, 1))

    def test_held_pressure_at_show_and_contact_flicker_do_not_retrigger(self):
        self.frames.reset()
        for index, touch in enumerate((1, 1, 1, 0, 1, 1, 1)):
            sample = self.frames.feed(pad(touch, force=(1100 if touch else 0, 0)), index * .004)
            self.assertFalse(sample and sample[5])
        for index, touch in enumerate((0, 0, 1, 1, 1)):
            sample = self.frames.feed(pad(touch, force=(1100 if touch else 0, 0)), .1 + index * .004)
        self.assertEqual(sample[5:7], (1, 1))

    def test_one_choice_when_pressure_switch_and_trigger_overlap(self):
        packets = [(1100, 1, 0), (1200, 0, 0), (1300, 0, 0), (1250, 0, 9000),
                   (1250, 1, 9000), (1150, 0, 0), (300, 0, 0), (960, 0, 0),
                   (960, 0, 0), (960, 0, 0), (2100, 1, 0)]
        edges = []
        for index, (force, click, trigger) in enumerate(packets):
            sample = self.frames.feed(pad(1, force=(force, 0), clicks=click, triggers=(trigger, 0)), index * .004)
            if sample and sample[5]:
                edges.append(index)
        self.assertEqual(edges, [0, 9])

    def test_repeated_trigger_presses_with_a_resting_thumb(self):
        for index, trigger in enumerate((0, 8500, 0, 9000, 0)):
            sample = self.frames.feed(pad(1, force=(600, 0), triggers=(trigger, 0)), index * .004)
            self.assertEqual(sample[5] if sample else 0, int(trigger != 0))

    def test_overlapping_trigger_and_click_select_once_per_side(self):
        sample = self.frames.feed(pad(3, clicks=3, triggers=(16000, 16000)), .1)
        self.frames.confirm(sample[-1], 3, 0, .102)
        self.send.assert_called_once_with(42, 3, PRESETS[2])
        self.assertIsNone(self.frames.feed(pad(3, triggers=(5500, 5500)), .106))
        self.frames.feed(pad(3), .112)
        next_sample = self.frames.feed(pad(3, triggers=(9300, 9300)), .116)
        self.frames.confirm(next_sample[-1], 3, 3, .117)
        self.assertEqual(self.send.call_args_list, [call(42, 3, PRESETS[2])] * 2)

    def test_one_report_touch_dropout_does_not_rearm_held_switch(self):
        first = self.frames.feed(pad(1, clicks=1), .1)
        self.frames.confirm(first[-1], 1, 0, .101)
        self.frames.feed(pad(0, clicks=1), .104)
        restored = self.frames.feed(pad(1, clicks=1), .108)
        self.assertEqual(restored[5:7], (0, 1))
        self.frames.confirm(restored[-1], 1, 1, .109)
        self.send.assert_called_once_with(42, 1, PRESETS[2])

    def test_disabled_preset_silences_all_pad_events(self):
        self.frames.feedback.preset = None
        touch = self.frames.feed(pad(3), .1)
        self.frames.confirm(touch[-1], 0, 3, .101)
        press = self.frames.feed(pad(3, clicks=3), .104)
        self.frames.confirm(press[-1], 3, 0, .105)
        self.send.assert_not_called()

    def test_hidden_duplicate_stale_and_touchscreen_frames_cannot_vibrate(self):
        p = Pilot(Focus(), lambda msg: None, None)
        p.frames = self.frames
        p.target = 'original'
        p.token = 7
        sample = self.frames.feed(pad(1, clicks=1), .1)
        event = {'op': 'feedback', 'token': 7, 'serial': sample[-1], 'clicked': 1, 'cues': 0}
        p.event(event, .11)
        p.visible = True
        p.event({**event, 'token': 6}, .12)
        p.event({**event, 'serial': -1}, .13)
        p.event(event, .5)
        p.stop()
        self.send.assert_not_called()

    def test_hiding_before_acknowledgement_discards_click(self):
        sample = self.frames.feed(pad(1, clicks=1), .1)
        self.frames.reset()
        self.frames.confirm(sample[-1], 1, 0, .11)
        self.send.assert_not_called()
