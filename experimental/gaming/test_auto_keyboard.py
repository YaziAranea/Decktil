import unittest
from unittest.mock import Mock, patch

from auto_keyboard import AutoKeyboard
from manual import ManualPilot
from test_controller import report
from test_manual import Window
from test_session import Output


class AutoKeyboardTests(unittest.TestCase):
    def setUp(self):
        self.window = Window()
        self.window.target = lambda **_: self.window.current
        self.messages = []
        self.output = Output()
        self.factory = Mock(return_value=self.output)
        self.warm = Mock()
        self.panel = ManualPilot(self.window, self.messages.append, self.factory, self.warm, transient=True)
        self.request = Mock(return_value=True)
        self.auto = AutoKeyboard(self.panel, self.request)
        self.ready()

    def ready(self):
        self.panel.event({'op': 'ready', 'xid': 28}, 0)

    def field(self, kind='editor', key=1, at=.1):
        self.auto.observe(407, (kind, key), at)

    def shown(self):
        self.panel.event({'op': 'shown', 'token': self.panel.token}, .2)

    def test_paired_down_before_space_recovers_only_a_genuine_left_selection_once(self):
        key = ('page', 123, 'search', (3,))
        self.field(key=key)
        self.shown()
        for driver in (self.auto, self.panel):
            driver.report(report(touch=1), .24)
        self.field('other', None, .25)
        self.assertTrue(self.panel.probe_space)
        for driver in (self.auto, self.panel):
            driver.report(report(touch=1, click=1), .52)
        frame = self.messages[-1]
        self.assertEqual(frame['op'], 'probeSpace')
        msg = {'op': 'spaceIntent', 'token': self.panel.token, 'serial': frame['values'][9]}
        with patch('auto_keyboard.recover', return_value='restored') as restore, \
             patch.object(self.panel.frames, 'confirm') as confirm:
            self.panel.event({**msg, 'serial': -1}, .53)
            self.assertFalse(self.output.received)
            self.panel.event(msg, .54)
            self.panel.event(msg, .55)
            restore.assert_called_once_with((407, key))
            confirm.assert_called_once_with(msg['serial'], 1, 0, .54)
        self.assertEqual(self.output.received, [' '])
        self.assertEqual(self.auto.recent_editor(.56), (407, key))
        self.assertTrue(self.panel.visible and not self.panel.native_handoff)

    def test_down_after_committed_space_restores_focus_without_retyping(self):
        key = ('page', 123, 'search', (3,))
        self.field(key=key)
        self.shown()
        self.auto.report(report(touch=1), .28)
        with patch('auto_keyboard.recover', side_effect=('focused', 'restored')) as restore:
            self.panel.event({'op': 'text', 'token': self.panel.token, 'text': ' '}, .3)
            self.field('other', None, .42)
            self.assertEqual(restore.call_count, 2)
        self.assertEqual(self.output.received, [' '])
        self.assertFalse(self.panel.native_handoff)

    def test_unrelated_control_or_real_back_button_cannot_receive_or_recover_space(self):
        self.field(key=('page', 123, 'search', (3,)))
        self.shown()
        self.auto.report(report(touch=1), .3)
        with patch('auto_keyboard.recover', return_value='blocked') as restore:
            self.panel.event({'op': 'text', 'text': ' ', 'token': self.panel.token}, .31)
            self.assertFalse(self.output.received)
            back = bytearray(report(touch=1))
            back[8] = 0x20
            self.auto.report(back, .35)
            self.field('other', None, .37)
            self.assertFalse(self.auto.select_space(.38))
            restore.assert_called_once()

    def test_repeated_steam_navigation_during_same_held_space_restores_without_another_write(self):
        key = ('page', 123, 'search', (3,))
        self.field(key=key)
        self.shown()
        held = report(touch=1, click=1, xy=(100, -23000, 0, 0))
        self.auto.report(held, .28)
        with patch('auto_keyboard.recover', return_value='restored') as restore:
            self.panel.event({'op': 'text', 'token': self.panel.token, 'text': ' '}, .3)
            for stamp in (.62, .91):
                self.auto.report(held, stamp)
                self.field('other', None, stamp + .01)
                restore.assert_called_with((407, key), held=True)
                self.assertFalse(self.panel.native_handoff)
            self.auto.report(report(), 1.12)
            self.field('other', None, 1.17)
            self.assertEqual(restore.call_count, 3)
            self.assertEqual(self.output.received, [' '])

    def test_auto_open_once_and_respect_hide_until_reactivated(self):
        self.field()
        self.shown()
        self.field()
        self.assertEqual(len(self.messages), 1)
        self.panel.chord(.3)
        self.field(at=.4)
        self.field(at=4)
        self.assertEqual([m['op'] for m in self.messages], ['show', 'hide'])
        self.field('other', None, 5)
        self.field(at=6)
        self.assertEqual(self.messages[-1]['op'], 'show')
        self.request.assert_not_called()

    def test_cold_panel_prewarms_on_steam_home_without_mapping(self):
        self.panel.ready = False
        self.auto.observe(None, None, .1)
        self.warm.assert_not_called()
        self.field('other', None)
        self.warm.assert_called_once()
        self.assertFalse(self.messages)
        self.factory.assert_not_called()
        self.request.assert_not_called()

    def test_cover_maps_before_dismiss_and_only_original_editor_receives_input(self):
        self.panel.ready = False
        self.field('inline')
        self.request.assert_not_called()
        self.assertTrue(self.warm.called)
        self.ready()
        self.window.editor = lambda: None
        self.field('inline')
        self.assertTrue(self.panel.native_handoff)
        self.assertEqual([m['op'] for m in self.messages], ['show'])
        self.assertTrue(self.messages[0]['cover'])
        self.shown()
        self.field('inline')
        self.request.assert_called_once_with('close')
        self.factory.assert_called_once_with(407)
        key = {'op': 'text', 'token': self.panel.token, 'text': 'я'}
        self.panel.event(key, .3)
        self.assertEqual(self.output.received, [])
        self.window.editor = self.window.target
        self.field(at=.4)
        self.assertEqual([m['op'] for m in self.messages], ['show', 'cover'])
        self.assertFalse(self.messages[-1]['active'])
        self.panel.event(key, .5)
        self.assertEqual(self.output.received, ['я'])
        self.assertIs(self.output, self.panel.sink)
        self.factory.assert_called_once()

    def test_foreign_focus_before_mapping_leaves_native_alone(self):
        self.field('inline')
        self.window.current = 912
        self.auto.observe(None, None, .3)
        self.assertTrue(self.output.closed)
        self.shown()
        self.assertEqual([m['op'] for m in self.messages], ['show', 'hide'])
        self.request.assert_not_called()

    def test_keep_native_on_output_failure_or_foreign_dialog(self):
        self.factory.side_effect = OSError()
        self.field('other', None)
        self.field('inline')
        self.field('inline')
        self.factory.assert_called_once()
        self.request.assert_not_called()

    def test_same_closing_chord_does_not_reopen_either_keyboard(self):
        self.field()
        self.shown()
        self.panel.chord(.3)
        self.field('inline', at=.5)
        self.field(at=.7)
        self.request.assert_called_once_with('close')
        self.assertIsNone(self.panel.target)
        # A later deliberate activation of the same field is a new native opening.
        self.field('inline', at=2)
        self.field(at=2.2)
        self.assertTrue(self.panel.show_pending)

    def test_missing_cover_ack_releases_output_and_leaves_native_open(self):
        self.field('inline')
        self.field('inline', at=.3)
        self.auto.tick(4)
        self.assertTrue(self.output.closed)
        self.field('inline', at=5)
        self.request.assert_not_called()

    def test_one_retry_for_a_persistent_same_inline_then_return_to_steam(self):
        self.field('inline')
        self.shown()
        for stamp in (.3, .5, .98, 1.5, 2.1):
            self.field('inline', at=stamp)
        self.assertEqual([c.args for c in self.request.call_args_list], [('close',), ('close',)])
        self.auto.tick(3)
        self.panel.tick(3)
        self.assertTrue(self.output.closed)
        self.field('inline', at=3.1)
        self.assertIsNone(self.panel.target)

    def test_separate_native_window_during_cover_prevents_close(self):
        self.field('inline')
        self.shown()
        self.window.native = True
        self.field('inline', at=.3)
        self.assertTrue(self.output.closed)
        self.request.assert_not_called()

    def test_held_a_cannot_reopen_for_input_after_the_preview_timed_out(self):
        self.field()
        self.shown()
        self.auto.report(report(), .25)
        self.auto.report(report(a=True), .26)
        self.field(at=.9)
        self.panel.tick(1.1)
        self.field(at=1.2)
        self.assertIsNone(self.panel.target)
        self.auto.report(report(), 1.27)
        self.field(at=1.55)
        self.assertTrue(self.panel.show_pending)
        self.request.assert_not_called()

    def test_physical_activation_starts_cover_before_stock_even_from_manually_hidden_panel(self):
        self.field()
        self.shown()
        self.panel.chord(.3)
        self.field(at=.4)
        self.auto.report(report(), .41)
        self.auto.report(report(a=True), .42)
        self.assertTrue(self.panel.show_pending and self.panel.native_handoff)
        self.panel.event({'op': 'shown', 'token': self.panel.token}, .48)
        self.auto.observe(407, ('editor', 1), .49, sampled_at=.39)
        self.assertTrue(self.panel.native_handoff)
        self.request.assert_not_called()
        self.auto.report(report(), .52)
        self.field('inline', at=.54)
        self.panel.stock([(True, 769)], .56)
        self.field(at=.79)
        self.assertTrue(self.panel.visible)
        self.assertFalse(self.panel.native_handoff)
        self.request.assert_called_once_with('close')

    def test_activation_that_did_not_open_stock_returns_to_previous_visibility(self):
        for visible in (True, False):
            with self.subTest(visible=visible):
                self.setUp()
                self.field()
                self.shown()
                if not visible:
                    self.panel.chord(.22)
                    self.field(at=.24)
                self.auto.report(report(), .25)
                self.auto.report(report(a=True), .26)
                if not visible:
                    self.shown()
                self.field(at=.48)
                self.assertTrue(self.panel.native_handoff)
                self.auto.report(report(), .52)
                self.field(at=.75)
                self.assertEqual(self.panel.visible, visible)
                self.assertFalse(self.panel.native_handoff)
                self.request.assert_not_called()

    def test_native_open_over_visible_panel_reuses_window_device_and_token(self):
        self.field()
        self.shown()
        original = self.panel.token
        self.field('other', None, .25)
        self.assertTrue(self.panel.native_handoff)
        self.field('inline', at=.45)
        self.assertTrue(self.panel.visible)
        self.assertTrue(self.panel.native_handoff)
        press = {'op': 'text', 'token': original, 'text': 'й'}
        self.panel.event(press, .35)
        self.assertFalse(self.output.received)
        self.field(at=.5)
        self.panel.event(press, .6)
        self.assertEqual(self.output.received, ['й'])
        self.assertEqual(self.panel.token, original)
        self.assertFalse(self.panel.native_handoff)
        self.assertNotIn('hide', [m['op'] for m in self.messages])
        self.factory.assert_called_once()
        self.request.assert_called_once_with('close')

    def test_losing_editor_metadata_closes_only_the_auto_opened_panel(self):
        self.field()
        self.shown()
        self.auto.observe(407, None, .5)
        self.auto.tick(3)
        self.assertIsNone(self.panel.target)
        self.assertTrue(self.output.closed)

    def test_real_blur_within_same_window_stops_input_before_unmapping(self):
        self.field()
        self.shown()
        self.field('other', None, .3)
        self.panel.event({'op': 'text', 'token': self.panel.token, 'text': 'x'}, .35)
        self.assertFalse(self.output.received)
        self.auto.tick(1.1)
        self.panel.tick(1.1)
        self.assertIsNone(self.panel.target)

    def test_native_reaching_the_window_guard_before_editor_poll_keeps_same_panel(self):
        self.field()
        self.shown()
        self.window.matches = lambda owner: False
        self.panel.tick(.3)
        self.assertTrue(self.panel.native_handoff and self.panel.visible)
        self.field('inline', at=.55)
        self.request.assert_called_once_with('close')
        self.window.matches = lambda owner: owner == self.window.current
        self.field(at=.8)
        self.assertFalse(self.panel.native_handoff)
        self.assertEqual([m['op'] for m in self.messages].count('show'), 1)
        self.assertNotIn('hide', [m['op'] for m in self.messages])
