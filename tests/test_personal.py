"""The personal model is opt-in, bounded and isolated from the system dictionaries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import bridge
import personal


class PersonalDictionary(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, XDG_DATA_HOME=str(self.root)))
        self.db = self.root / "decktil" / "personal.sqlite3"

    def test_defaults_off_without_creating_files(self):
        self.assertFalse(personal.enabled())
        personal.learn("old", "en", "", "", "hello")
        personal.set_enabled(False)
        personal.clear()
        self.assertEqual(personal.suggestions("en", "", "", "he"), [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_ranking_persistence_and_isolation(self):
        personal.set_enabled(True)
        token = personal.session()
        self.assertTrue(token)
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.db.parent.stat().st_mode & 0o777, 0o700)
        def use(a, b, word):
            personal.learn(token, "en_US", a, b, word)
        use("let", "us", "flutter")
        self.assertEqual(personal.suggestions("en", "", "", "fl"), [])
        use("let", "us", "flutter")
        for _ in range(4):
            use("they", "both", "flourish")
        self.assertEqual(personal.suggestions("en", "", "", "fl"), ["flourish", "flutter"])
        self.assertEqual(personal.suggestions("en", "let", "us", "fl"), ["flutter", "flourish"])
        self.assertEqual(personal.suggestions("en", "", "us", ""), ["flutter"])
        self.assertEqual(personal.suggestions("en", "", "", ""), [])
        self.assertEqual(personal.suggestions("en", "", "", "flutter"), [])
        for _ in range(2):
            use("another", "turn", "I’m")
            personal.learn(token, "ru_RU", "", "лови", "Ёлочку")
        self.assertEqual(personal.suggestions("en", "", "", "i’"), ["i'm"])
        self.assertEqual(personal.suggestions("ru", "", "", "ё"), ["ёлочку"])
        self.assertEqual(personal.suggestions("en", "", "", "ё"), [])
        self.assertEqual(personal.suggestions("ru", "", "", "fl"), [])
        self.assertEqual(personal.suggestions("en", "", "", "fl%"), [])

    def test_learned_typos_require_repetition_and_follow_direct_completions(self):
        personal.set_enabled(True)
        token = personal.session()
        for lang, word, typo, direct in (("en", "merlux", "nerlux", "nerluxes"),
                                         ("ru", "зорвик", "зоврик", "зоврики")):
            with self.subTest(lang=lang):
                personal.learn(token, lang, "", "", word)
                self.assertEqual(personal.suggestions(lang, "", "", typo), [])
                personal.learn(token, lang, "", "", word)
                self.assertEqual(personal.suggestions(lang, "", "", typo), [word])
                self.assertEqual(personal.suggestions(lang, "", "", word), [])
                for _ in range(2):
                    personal.learn(token, lang, "", "", direct)
                self.assertEqual(personal.suggestions(lang, "", "", typo), [direct, word])
        personal.set_enabled(False)
        self.assertEqual(personal.suggestions("en", "", "", "nerlux"), [])
        self.assertEqual(personal.suggestions("ru", "", "", "зоврик"), [])

    def test_pause_clear_and_late_word(self):
        personal.set_enabled(True)
        first = personal.session()
        for _ in range(2):
            personal.learn(first, "en", "see", "you", "soon")
        personal.set_enabled(False)
        self.assertFalse(personal.enabled())
        self.assertEqual(personal.suggestions("en", "see", "you", ""), [])
        personal.set_enabled(True)
        second = personal.session()
        self.assertNotEqual(first, second)
        personal.set_enabled(True)
        self.assertEqual(second, personal.session())
        self.assertEqual(personal.suggestions("en", "see", "you", ""), ["soon"])
        personal.clear()
        third = personal.session()
        self.assertTrue(third)
        self.assertNotIn(third, [first, second])
        for generation in [first, first, second, second]:
            personal.learn(generation, "en", "see", "you", "soon")
        self.assertEqual(personal.suggestions("en", "see", "you", ""), [])
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM entries").fetchone()[0], 0)

    def test_invalid_words_never_stored_and_limit_is_per_language(self):
        personal.set_enabled(True)
        token = personal.session()
        for invalid in ("https://some.org", "a@b.net", "abc123", "two words", "", "f" * 33, "secret_id"):
            personal.learn(token, "en", "", "", invalid)
        personal.learn(token, "es", "", "", "hola")
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM entries").fetchone()[0], 0)
        with patch.object(personal, "MAX_ENTRIES_PER_LANGUAGE", 4):
            for _ in range(4):
                personal.learn(token, "en", "", "", "apple")
            for sample in ("banana", "cider", "date", "elder", "fig", "grape"):
                personal.learn(token, "en", "", "", sample)
            personal.learn(token, "ru", "снова", "лови", "рыбку")
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(dict(db.execute("SELECT lang, COUNT(*) FROM entries GROUP BY lang")),
                             {"en": 4, "ru": 3})
        self.assertEqual(personal.suggestions("en", "", "", "ap"), ["apple"])

    def test_multiple_senders_accumulate_each_word(self):
        personal.set_enabled(True)
        token = personal.session()
        def type_once(_):
            personal.learn(token, "en", "", "", "tiny")
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(type_once, range(18)))
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("SELECT uses FROM entries").fetchall(), [(18,)])

    def test_bridge_hides_suggestions_and_drops_input_while_inactive(self):
        personal.set_enabled(True)
        adapter = object.__new__(bridge.Bridge)
        adapter.visible = True
        adapter.locked = adapter.in_game = False
        tok = adapter.PersonalSession()
        for _ in range(2):
            adapter.LearnWord(tok, "en", "", "", "quibble")
        self.assertEqual(adapter.PersonalSuggestions("en", "", "", "qui"), ["quibble"])
        for flag, value in (("locked", True), ("visible", False), ("in_game", True)):
            with patch.object(adapter, flag, value):
                self.assertEqual(adapter.PersonalSuggestions("en", "", "", "qui"), [])
                adapter.LearnWord(tok, "en", "", "", "quiver")
        self.assertEqual(adapter.PersonalSuggestions("en", "", "", "qui"), ["quibble"])

    def test_reading_broken_store_fails_closed(self):
        self.db.parent.mkdir()
        self.db.write_bytes(b"unusable database")
        self.assertFalse(personal.enabled())


if __name__ == "__main__":
    unittest.main()
