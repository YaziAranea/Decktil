import unittest

from session import Pilot
from test_controller import report


class Focus:
    current = 'original'
    keyboard = False
    search_confirmed = False

    def editor(self):
        return self.current

    def inline(self, editor):
        return self.keyboard if editor == self.current else None

    def clear_to_wait(self, editor):
        return self.search_confirmed and editor == self.current


class Output:
    def __init__(self):
        self.received = []
        self.closed = False

    def text(self, value, guard):
        if guard():
            self.received.append(value)
            return True
        return False

    def close(self):
        self.closed = True


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.focus, self.out = Focus(), Output()
        self.panel, self.steam = [], []
        self.plane_free = True
        self.p = Pilot(self.focus, self.panel.append, self.request, output=lambda: self.out,
                       plane_available=lambda xid: xid == 43 and self.plane_free)
        self.p.event({'op': 'ready', 'xid': 43}, 0)

    def request(self, action):
        self.steam.append(action)
        return True

    def chord(self, at=0):
        self.p.report(report(), at)
        self.p.report(report(guide=True, x=True), at + .01)
        self.p.report(report(), at + .02)

    def handoff(self):
        self.chord()
        self.focus.keyboard = True
        self.p.stock([(True, 769)], .1)
        self.p.tick(.11)
        self.assertEqual(self.steam, ['close'])
        self.assertFalse(self.panel)
        self.focus.keyboard = False
        self.p.stock([(False, 769)], .2)
        self.p.tick(.21)
        self.assertEqual(self.panel, [{'op': 'show', 'token': 1}])
        self.p.event({'op': 'shown', 'token': 1}, .22)

    def test_output_is_bound_to_show_token_and_original_field(self):
        self.handoff()
        self.p.event({'op': 'text', 'text': 'old', 'token': 0}, .23)
        self.p.event({'op': 'text', 'text': 'ф', 'token': 1}, .24)
        self.focus.current = 'new field'
        self.p.event({'op': 'text', 'text': 'wrong target', 'token': 1}, .25)
        self.assertEqual(self.out.received, ['ф'])
        self.assertTrue(self.out.closed)
        self.assertEqual(self.panel[-1], {'op': 'hide'})
        self.assertEqual(self.steam, ['close'])

    def test_unknown_popup_never_receives_a_close_request(self):
        self.chord()
        self.p.stock([(True, 769)], .1)
        self.focus.keyboard = None
        self.p.tick(.11)
        self.p.tick(2)
        self.assertFalse(self.panel or self.steam)
        self.assertFalse(self.out.received)
        self.assertIsNone(self.p.sink)

    def test_first_confirmed_search_chord_waits_for_real_open_before_connecting_output(self):
        self.focus.keyboard = None
        self.focus.search_confirmed = True
        self.chord()
        self.assertEqual(self.p.gate.stage, 'await_open')
        self.assertIsNone(self.p.sink)
        self.assertFalse(self.steam)
        self.focus.keyboard = True
        self.p.stock([(True, 769)], .1)
        self.p.tick(.11)
        self.assertIs(self.p.sink, self.out)
        self.assertEqual(self.steam, ['close'])

    def test_restore_original_keyboard_if_panel_fails_to_show(self):
        self.chord()
        self.focus.keyboard = True
        self.p.stock([(True, 769)], .1)
        self.p.tick(.11)
        self.focus.keyboard = False
        self.p.stock([(False, 769)], .2)
        self.p.tick(.21)
        self.p.tick(3)
        self.assertEqual(self.steam, ['close', 'open'])
        self.assertTrue(self.out.closed)

    def test_focused_game_and_first_unknown_stock_state_keep_native_keyboard(self):
        self.focus.keyboard = None
        self.chord()
        self.assertIsNone(self.p.target)
        self.p.stock([(False, 769)], 1)
        self.focus.current = None
        self.chord(2)
        self.assertIsNone(self.p.target)
        self.assertFalse(self.panel or self.steam)

    def test_second_chord_closes_both_only_for_same_confirmed_inline_keyboard(self):
        self.handoff()
        self.chord(1)
        self.assertFalse(self.p.visible)
        self.assertTrue(self.out.closed)
        self.focus.keyboard = True
        self.p.stock([(True, 769)], 1.1)
        self.p.tick(1.11)
        self.focus.keyboard = False
        self.p.stock([(False, 769)], 1.2)
        self.p.tick(1.21)
        self.assertIsNone(self.p.target)
        self.assertEqual(self.steam, ['close', 'close'])
        self.assertEqual([item['op'] for item in self.panel if item['op'] != 'frame'], ['show', 'hide'])

    def test_game_request_after_toggle_keeps_its_native_dialog(self):
        self.handoff()
        self.chord(1)
        self.p.stock([(True, 87)], 1.1)
        self.p.tick(1.2)
        self.assertIsNone(self.p.target)
        self.assertEqual(self.steam, ['close'])

    def test_steam_open_before_physical_close_chord_is_consumed_once(self):
        self.handoff()
        self.focus.keyboard = True
        self.p.stock([(True, 769)], 1)
        self.assertFalse(self.p.visible)
        self.assertEqual(self.steam, ['close'])
        self.chord(1.02)
        self.p.tick(1.05)
        self.assertEqual(self.steam, ['close', 'close'])
        self.focus.keyboard = False
        self.p.stock([(False, 769)], 1.1)
        self.p.tick(1.11)
        self.assertIsNone(self.p.target)
        self.assertEqual(self.p.token, 1)

    def test_reordered_chord_requires_same_inline_editor_and_fresh_native_open(self):
        for field, appid, delay, inline in (
            ('another field', 769, .02, True), ('original', 10, .02, True),
            ('original', 769, .4, True), ('original', 769, .02, None),
        ):
            with self.subTest(field=field, appid=appid, delay=delay, inline=inline):
                self.setUp()
                self.handoff()
                self.focus.keyboard = inline
                self.p.stock([(True, appid)], 1)
                self.focus.current = field
                self.chord(1 + delay)
                self.p.tick(1 + delay + .05)
                self.p.tick(4)
                self.assertEqual(self.steam, ['close'])
                self.assertIsNone(self.p.target)

    def test_first_pad_click_after_shown_is_delivered_and_disconnect_releases_overlay(self):
        self.handoff()
        self.p.report(report(touch=1, click=1), .23)
        self.assertEqual(self.panel[-1]['values'][5:7], (1, 1))
        self.p.device_lost()
        self.assertFalse(self.p.visible)
        self.assertIsNone(self.p.last_report_at)
        self.assertIsNone(self.p.sink)
        self.assertEqual(self.panel[-1], {'op': 'hide'})
        self.assertEqual(self.steam, ['close'])
        self.p.report(report(guide=True, x=True), 3)
        self.assertIsNone(self.p.target)

    def test_competing_plane_before_dismiss_keeps_steam_and_after_show_restores_it(self):
        self.chord()
        self.plane_free = False
        self.focus.keyboard = True
        self.p.stock([(True, 769)], .1)
        self.p.tick(.11)
        self.assertFalse(self.steam or self.panel)
        self.assertIsNone(self.p.target)
        self.focus.keyboard = False
        self.p.stock([(False, 769)], .2)
        self.chord(.3)
        self.assertIsNone(self.p.target)
        self.plane_free = True
        self.handoff()
        self.plane_free = False
        self.p.tick(2)
        self.assertEqual(self.steam, ['close', 'open'])
        self.assertEqual(self.panel[-1], {'op': 'hide'})
