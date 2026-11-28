import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORDS = ROOT / "src" / "words"
sys.path.insert(0, str(ROOT / "tools"))
import build_predictions as builder


class PredictionDataTests(unittest.TestCase):
    def test_can_rebuild_both_models_offline(self):
        with tempfile.TemporaryDirectory() as output:
            subprocess.run([sys.executable, ROOT / "tools" / "build_predictions.py",
                            "--output-dir", output], check=True, capture_output=True)
            for lang in ("en", "ru"):
                filename = f"{lang}-next.json.gz"
                with self.subTest(lang=lang):
                    self.assertEqual((Path(output) / filename).read_bytes(),
                                     (WORDS / filename).read_bytes())

    def test_common_conversation_chains(self):
        examples = {
            "en": {"hello": "how", "hello how": "are", "thank you": "for", "i don't": "know",
                   "i think": "we", "skill": "issue", "grab some": "loot", "everyone typed": "gg",
                   "plugin": "settings", "commit message": "describes", "deployment pipeline": "is",
                   "awesome": "work", "the repository": "contains", "the download": "has"},
            "ru": {"привет": "как", "привет как": "дела", "спасибо за": "помощь", "я не": "могу",
                   "я могу": "помочь", "лови": "инвайт", "это полный": "кринж", "все написали": "гг",
                   "варить": "суп", "сделать снимок": "экрана", "клавиатура": "работает",
                   "планшет": "подключён", "подключить": "плагин", "отключить": "плагин",
                   "настроить вебхук": "в", "перед коммитом": "запусти", "токен доступа": "истёк"},
        }
        for lang, pairs in examples.items():
            with gzip.open(WORDS / f"{lang}-next.json.gz", "rt", encoding="utf-8") as stream:
                tables = json.load(stream)
            self.assertEqual(tables["format"], 1)
            for context, expected in pairs.items():
                with self.subTest(lang=lang, context=context):
                    table = tables["trigram" if " " in context else "bigram"]
                    self.assertIn(expected, table[context][:3])

    def test_rare_phrases_require_independent_support(self):
        counts, documents = builder.count_sentences([
            "bright lights glow", "bright lights flicker", "distant lights glow",
            "city lights flicker", "bright lights shudder",
            "walk south walk north", "walk south walk north"
        ], "en")
        rare = builder.rare_continuations(counts, documents)
        self.assertEqual(rare["bright lights", "glow"], 2)
        self.assertEqual(rare["bright lights", "flicker"], 2)
        self.assertNotIn(("bright lights", "shudder"), rare)
        self.assertEqual(documents["walk"], 1)
        self.assertFalse(any(context == "walk" for context, _ in rare))

    def test_suffix_votes_come_from_distinct_predecessors(self):
        candidates = {"walking": {"to": 3, "nowhere": 9999}, "talking": {"to": 2},
                      "jumping": {"to": 5}, "running": {"out": 5}}
        suffix = builder.suffix_model(candidates)
        self.assertEqual(suffix["ing"], ["to"])
        self.assertNotIn("ng", suffix)
        for lang, ending, expected in (("ru", "ться", "с"), ("en", "tion", "of")):
            with gzip.open(WORDS / f"{lang}-next.json.gz", "rt", encoding="utf-8") as source:
                table = json.load(source)["suffix"]
            self.assertIn(expected, table[ending][:3])
            self.assertTrue(all(2 <= len(key) <= 4 and 1 <= len(words) <= 5
                                for key, words in table.items()))


if __name__ == "__main__":
    unittest.main()
