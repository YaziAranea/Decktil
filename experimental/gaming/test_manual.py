import unittest
from unittest.mock import Mock

from manual import GamingSession, ManualPilot
from session import Pilot
from test_controller import report
from test_session import Focus, Output


class Window:
    current = 407
    native = False
    reason = 'test-window'
    panel_xid = None

    def target(self):
        return self.current

    editor = target

    def matches(self, xid):
        return self.target() == xid

    def native_visible(self):
        return self.native


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.window, self.output = Window(), Output()
        self.sent, self.warms = [], []
        self.manual = ManualPilot(self.window, self.sent.append, lambda xid: self.output,
                                  lambda: self.warms.append(True), transient=True)

    def chord(self, p, now=0):
        p.report(report(), now)
        p.report(report(guide=True, x=True), now+.01)
        p.report(report(), now+.08)

    def test_first_press_warms_and_shows_after_release_then_toggles(self):
        p = self.manual
        self.chord(p)
        p.tick(.2)
        self.assertTrue(self.warms)
        self.assertFalse(self.sent)
        p.event({'op':'ready','xid':52}, .35)
        p.tick(.36)
        self.assertEqual(self.sent, [{'op':'show','owner':407,'token':1}])
        p.event({'op':'shown','token':1}, .37)
        p.event({'op':'text','text':'й','token':1}, .38)
        self.assertEqual(self.output.received, ['й'])
        self.chord(p, 1)
        self.assertIsNone(p.target)
        self.assertEqual(self.sent[-1], {'op':'hide'})

    def test_different_focus_native_keyboard_and_foreign_request_veto_before_show(self):
        for change in ('focus','window','external-request'):
            with self.subTest(change=change):
                self.setUp()
                p = self.manual
                p.event({'op':'ready','xid':52}, 0)
                self.chord(p)
                if change == 'focus':
                    self.window.current = 408
                elif change == 'window':
                    self.window.native = True
                else:
                    p.stock([(True, 58)], .12)
                p.tick(.5)
                self.assertFalse(self.sent)
                self.assertIsNone(p.target)

    def test_child_decline_cancels_pending_show(self):
        p = self.manual
        p.event({'op':'ready','xid':52}, 0)
        self.chord(p)
        p.tick(.5)
        p.event({'op':'hide','token':1}, .51)
        p.event({'op':'shown','token':1}, .52)
        self.assertFalse(p.visible)

    def test_language_key_requires_visible_panel_current_token_and_original_window(self):
        layout = Mock()
        layout.locale.return_value = 'ru_RU'
        p = self.manual
        p.layout_factory = lambda guard: layout
        p.event({'op': 'ready', 'xid': 52}, 0)
        self.chord(p)
        p.tick(.5)
        self.assertEqual(self.sent[-1]['locale'], 'ru_RU')
        press = {'op': 'locale', 'token': 1, 'locale': 'en_US'}
        p.event(press, .51)
        p.event({'op': 'shown', 'token': 1}, .52)
        p.event({**press, 'token': 0}, .53)
        layout.select.assert_not_called()
        layout.locale.return_value = 'en_US'
        p.event(press, .54)
        layout.select.assert_called_once_with('en_US')
        self.assertEqual(self.sent[-1], press)
        self.window.current = 408
        p.event(press, .55)
        self.assertIsNone(p.target)
        layout.close.assert_called_once()

    def router(self):
        self.library = Pilot(Focus(), self.sent.append, lambda cmd: True, output=lambda: self.output,
                             plane_available=lambda xid: True)
        p = GamingSession(self.library, self.manual)
        p.event({'op':'ready','xid':52}, 0)
        p.stock([], 0)
        return p

    def test_unavailable_log_at_press_still_opens_for_verified_steam_window(self):
        p = self.router()
        p.stock(None, 0)
        self.chord(p)
        p.stock(None, .2)
        p.tick(.5)
        self.assertIs(p.current, self.manual)
        self.assertEqual(self.sent, [{'op':'show','owner':407,'token':1}])

    def test_stale_foreign_osk_edge_does_not_disable_steam_ui(self):
        p = self.manual
        p.event({'op': 'ready', 'xid': 52}, 0)
        p.stock([(True, 413080)], 2)
        self.chord(p, 2.1)
        self.assertIsNone(p.target)
        self.chord(p, 8)
        p.tick(8.5)
        self.assertEqual(self.sent, [{'op':'show','owner':407,'token':1}])

    def test_silent_gamepad_ui_and_no_accessible_tree_can_open_manually(self):
        for accessible in (True, False):
            with self.subTest(accessible=accessible):
                self.setUp()
                p = self.router()
                if not accessible:
                    self.library.focus.current = None
                self.chord(p)
                p.tick(1.5)
                p.tick(1.8)
                self.assertEqual(self.sent, [{'op':'show','owner':407,'token':1}])
                self.assertIs(p.current,self.manual)

    def test_recently_verified_field_uses_manual_cover_route_for_guide_x(self):
        p = self.router()
        p.recent_editor = lambda now: (407, 1)
        self.chord(p)
        self.assertIs(p.current, self.manual)
        self.assertTrue(self.manual.waiting)

    def test_unidentified_open_native_keyboard_is_never_taken_over(self):
        p = self.router()
        self.chord(p)
        self.library.focus.keyboard = None
        p.stock([(True,769)], .1)
        p.tick(1.5)
        p.tick(2.0)
        self.assertFalse(self.sent)
