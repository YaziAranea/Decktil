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

    def test_one_letter_of_second_word_then_immediate_next_suggestion(self):
        for first, second, following in [('спасибо', 'тебе', 'за'), ('thank', 'you', 'for')]:
            with self.subTest(first=first):
                self.event('resetPrediction')
                self.output.received.clear()
                self.event('text', text=first)
                self.event('text', text=' ')
                self.assertEqual(self.messages[-1]['text'], first + ' ')
                self.event('text', text=second[0])
                self.event('suggestion', revision=self.pilot.words.revision, prefix=second[0], word=second)
                self.assertEqual(self.messages[-1]['text'], first + ' ' + second + ' ')
                self.event('suggestion', revision=self.pilot.words.revision, prefix='', word=following)
                self.assertEqual(self.output.received,
                                 [first, ' ', second[0], second[1:] + ' ', following + ' '])

    def test_learning_requires_fresh_observation_and_only_panel_edits(self):
        field = (self.focus.current, self.key)
        for ch in "qt":
            self.event("text", text=ch, generation="test")
        self.assertFalse(any(m["op"] == "personalEdit" for m in self.messages))
        self.pilot.confirm_learning(field, .99)
        self.assertFalse(any(m["op"] == "personalEdit" for m in self.messages))
        self.pilot.confirm_learning(field, 1.1)
        edits = [m["edit"] for m in self.messages if m["op"] == "personalEdit"]
        self.assertEqual([edit["text"] for edit in edits], list("qt"))
        self.assertTrue(all(edit["generation"] == "test" for edit in edits))
        self.event("text", text=" ")
        self.pilot.confirm_learning((self.focus.current, ("other",)), 1.2)
        self.assertEqual(self.messages[-1]["op"], "resetLearning")

        # Restoring the suffix after a Steam animation and a bulk insertion
        # can update predictions but do not represent individually typed keys.
        self.pilot.update_words(field, inserted="restored text")
        self.event("text", text="bulk text")
        self.pilot.confirm_learning(field, 2)
        self.assertEqual(sum(m["op"] == "personalEdit" for m in self.messages), 2)

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
        for kind in ("email", "url", "tel", "private", "password"):
            with self.subTest(kind=kind):
                self.key = ("page", 1, kind, (1, 2))
                for char in "private ":
                    self.event("text", text=char, generation="test")
                self.pilot.confirm_learning((self.focus.current, self.key), 2)
                self.assertEqual(self.pilot.words.text, "")
                self.assertFalse(any(m["op"] == "personalEdit" for m in self.messages))

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
