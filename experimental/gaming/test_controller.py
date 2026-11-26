"""Isolated checks for Deck reports and Steam keyboard handoff."""
from pathlib import Path
import struct
import tempfile
import unittest

from controller import ChordEdge, DeckFrames, ERASE_BUTTON, SteamLog, StockGate


def report(*, guide=False, x=False, a=False, touch=0, click=0, triggers=(0, 0), xy=(0, 0, 0, 0)):
    data = bytearray(64)
    data[:4] = b"\x01\x00\x09\x40"
    data[8] = (0x40 if x else 0) | (0x80 if a else 0)
    data[9] = 0x20 if guide else 0
    data[10] = touch << 3 | click << 1
    struct.pack_into("<hhhh", data, 16, *xy)
    struct.pack_into("<HH", data, 44, *triggers)
    return bytes(data)


class ControllerTests(unittest.TestCase):
    def test_chord_needs_fresh_x_after_baseline_even_when_guide_is_held(self):
        detector = ChordEdge()
        def feed(guide, x):
            return detector.feed(report(guide=guide, x=x))
        self.assertFalse(feed(True, True))
        self.assertFalse(feed(True, True))
        self.assertFalse(feed(True, False))
        self.assertTrue(feed(True, True))
        self.assertFalse(feed(False, True))
        self.assertFalse(feed(True, True))
        self.assertFalse(detector.feed(bytes(64)))
        self.assertFalse(feed(False, False))
        self.assertFalse(feed(False, True))
        self.assertFalse(feed(True, True))
        detector.reset()
        self.assertFalse(feed(True, True))

    def test_one_press_per_side_for_combined_trigger_and_click(self):
        frames = DeckFrames()
        self.assertEqual(frames.feed(report(touch=3, click=2), 0)[5:7], (0, 2))
        self.assertEqual(frames.feed(report(touch=3), .01)[5:7], (0, 0))
        self.assertEqual(frames.feed(report(touch=3, triggers=(9000, 0)), .02)[5:7], (1, 1))
        self.assertEqual(frames.feed(report(touch=3, click=1, triggers=(6500, 0)), .14)[5:7], (0, 1))
        self.assertEqual(frames.feed(report(guide=True), .15)[:9], (0,) * 9)
        self.assertIsNone(frames.feed(report(guide=True), .16))
        self.assertEqual(frames.feed(report(touch=1, click=1), .17)[5:7], (0, 1))

    def test_bare_x_has_one_erase_edge_per_press_and_heartbeats_without_pad_touch(self):
        frames = DeckFrames()
        frames.feed(report(), 0)
        self.assertEqual(frames.feed(report(x=True), .01)[4:7], (0, ERASE_BUTTON, ERASE_BUTTON))
        self.assertIsNone(frames.feed(report(x=True), .04))
        self.assertEqual(frames.feed(report(x=True), .12)[4:7], (0, 0, ERASE_BUTTON))
        self.assertEqual(frames.feed(report(), .13)[4:7], (0, 0, 0))
        self.assertEqual(frames.feed(report(x=True), .14)[5:7], (ERASE_BUTTON, ERASE_BUTTON))

    def test_chord_cancels_erase_and_x_held_through_open_does_not_start_deleting(self):
        frames = DeckFrames()
        self.assertEqual(frames.feed(report(x=True), 0)[5], 0)
        frames.feed(report(), .01)
        self.assertEqual(frames.feed(report(x=True), .02)[5], ERASE_BUTTON)
        release = frames.feed(report(guide=True, x=True), .04)
        self.assertEqual(release[4:7], (0, 0, 0))
        self.assertIsNone(frames.feed(report(guide=True, x=True), .05))
        self.assertEqual(frames.feed(report(x=True), .06)[5], 0)
        self.assertEqual(frames.feed(report(), .07)[5:7], (0, 0))

    def test_log_skips_history_partials_and_resets_on_rotation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "controller_ui.txt"
            path.write_bytes(b"[old] Set OSK active 1 and appid 769\n")
            log = SteamLog(path)
            self.assertIsNone(log.poll(7))
            with path.open("ab") as f:
                f.write(b"[now] Set OSK active ")
            self.assertEqual(log.poll(7), [])
            with path.open("ab") as f:
                f.write(b"1 and appid 769\n[now] Set OSK active 0 and appid 769\r\n")
            self.assertEqual(log.poll(7), [(True, 769), (False, 769)])
            path.rename(path.with_suffix(".old"))
            path.write_bytes(b"[old] Set OSK active 1 and appid 769\n")
            self.assertIsNone(log.poll(7))
            self.assertEqual(log.poll(7), [])
            self.assertIsNone(log.poll(17))

    def test_gate_requires_chord_then_known_inline_then_confirmed_close(self):
        gate, editor = StockGate(), (100, 42, 7)
        gate.arm(editor, 0, ready=True, stock_closed=True)
        gate.observe(True, 769, .1)
        self.assertIsNone(gate.advance(editor, .2, ready=True, inline=None))
        self.assertEqual(gate.advance(editor, .25, ready=True, inline=True), "dismiss-stock")
        self.assertIsNone(gate.advance(editor, .26, ready=True, inline=False))
        gate.observe(False, 769, .3)
        self.assertIsNone(gate.advance(editor, .31, ready=True, inline=True))
        self.assertEqual(gate.advance(editor, .32, ready=True, inline=False), "show")
        self.assertIsNone(gate.advance(editor, .33, ready=True, inline=False))

    def test_gate_rejects_other_editors_overlay_apps_missing_readiness_and_late_events(self):
        for target, ready, appid, stamp in ((None, True, 769, .1), (8, True, 769, .1),
                                            (7, False, 769, .1), (7, True, 19, .1),
                                            (7, True, 769, 3)):
            with self.subTest(target=target, ready=ready, appid=appid, stamp=stamp):
                gate = StockGate()
                gate.arm(7, 0, ready=True, stock_closed=True)
                gate.observe(True, appid, stamp)
                self.assertIsNone(gate.advance(target, stamp, ready=ready, inline=True))
                self.assertIsNone(gate.advance(7, stamp+.1, ready=True, inline=True))
        for closed in (False, None):
            gate = StockGate()
            gate.arm(7, 0, ready=True, stock_closed=closed)
            gate.observe(True, 769, .1)
            self.assertIsNone(gate.advance(7, .2, ready=True, inline=True))


if __name__ == "__main__":
    unittest.main()
