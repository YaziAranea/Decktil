"""Check the ranked dictionaries used for offline keyboard completions."""
import gzip
from pathlib import Path
import unittest


WORDS = Path(__file__).resolve().parents[1] / "src" / "words"


class CompletionDataTests(unittest.TestCase):
    def test_general_and_curated_vocabulary(self):
        examples = {
            "ru": [("прив", "привет"), ("вай", "вайб"), ("кри", "кринж"),
                   ("перезагр", "перезагрузка"), ("правопи", "правописание")],
            "en": [("hel", "help"), ("low", "lowkey"), ("respa", "respawn"),
                   ("y'", "y'all"), ("accessib", "accessibility"), ("autocor", "autocorrect")],
        }
        for lang, cases in examples.items():
            with self.subTest(lang=lang):
                with gzip.open(WORDS / f"{lang}.txt.gz", "rt", encoding="utf-8") as source:
                    words = source.read().splitlines()
                entries = set(words)
                self.assertGreaterEqual(len(entries), 130_000)
                additions = {"ru": {"автовокзале", "накопитель", "автозаполнение", "ыканье"},
                             "en": {"customizing", "multiplier", "autosaving", "keycaps"}}
                self.assertTrue(additions[lang] <= entries)
                self.assertEqual(len(entries), len(words))
                slang = (WORDS / "training" / f"{lang}-slang.txt").read_text(encoding="utf-8").splitlines()
                self.assertTrue(slang)
                self.assertTrue(set(slang).issubset(entries))
                for prefix, word in cases:
                    candidates = [entry for entry in words if entry != prefix and entry.startswith(prefix)]
                    self.assertIn(word, candidates[:3], prefix)
