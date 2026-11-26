import unittest

from session import Pilot
from test_controller import report
from test_session import Focus, Output


class WordCompletionTests(unittest.TestCase):
    def setUp(self):
        self.focus = Focus()
        self.messages = []
        self.output = Output()
        self.pilot = Pilot(self.focus, self.messages.append, None)
        self.pilot.token = 3
        self.pilot.visible = True
        self.pilot.target = self.focus.current
        self.pilot.sink = self.output
        self.key = ('page', 1, 'search', (1, 2))
        self.pilot.observed_editor = lambda now: (self.focus.current, self.key)

    def event(self, op, **fields):
        self.pilot.event(dict(token=3, op=op, **fields), 1)

    def test_complete_only_missing_letters_then_offer_the_next_word(self):
        self.event('text', text='варит')
        pick = dict(prefix='варит', word='варить', revision=self.pilot.words.revision)
        self.event('suggestion', **pick)
        self.event('suggestion', **pick)
        self.assertEqual(self.output.received, ['варит', 'ь '])
        self.assertEqual(self.messages[-1]['text'], 'варить ')
        self.event('suggestion', revision=self.pilot.words.revision, prefix='', word='суп')
        self.assertEqual(self.output.received[-1], 'суп ')

    def test_focus_change_and_locale_reset_invalidate_old_candidates(self):
        self.event('text', text='hel')
        pick = dict(prefix='hel', word='hello', revision=self.pilot.words.revision)
        self.key = ('page', 1, 'search', (1, 3))
        self.event('suggestion', **pick)
        self.event('text', text='hel')
        self.event('suggestion', **pick)
        self.event('resetPrediction')
        self.event('suggestion', **pick)
        self.assertEqual(self.output.received, ['hel', 'hel'])
        self.assertEqual(self.pilot.words.text, '')

    def test_field_types_and_navigation_reset_local_suffix(self):
        self.event('text', text='q')
        self.output.action = lambda name, guard: guard()
        self.event('action', action='backspace')
        self.assertEqual(self.pilot.words.text, '')
        self.event('text', text='q')
        self.event('action', action='left')
        self.assertEqual(self.pilot.words.text, '')
        self.key = ('page', 1, 'email', (1, 2))
        self.event('text', text='private')
        self.assertEqual(self.pilot.words.text, '')
        self.assertNotIn('private', [m.get('text') for m in self.messages])

    def test_stale_partial_prefix_cannot_change_a_word(self):
        self.event('text', text='shell')
        revision = self.pilot.words.revision
        for prefix, word in [('hell', 'hello'), ('shell', 'shall'), ('', 'word'), ('shell', 'shell X')]:
            self.event('suggestion', revision=revision, prefix=prefix, word=word)
        self.assertEqual(self.output.received, ['shell'])

    def test_r1_space_needs_a_visible_editor_and_a_fresh_unmodified_press(self):
        r1 = bytearray(report())
        r1[8] |= 4
        chord = bytearray(r1)
        chord[9] |= 0x20
        for packet in (r1, report(), chord, r1, report(), r1, r1, report(), r1):
            self.pilot.report(packet, 1)
        self.assertEqual(self.output.received, [' ', ' '])
        self.pilot.observed_editor = lambda now: None
        self.pilot.report(report(), 2)
        self.pilot.report(r1, 2.1)
        self.assertEqual(self.output.received, [' ', ' '])
