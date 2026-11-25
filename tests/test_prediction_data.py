import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORDS = ROOT / "src" / "words"


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
            "en": {"hello": "how", "hello how": "are", "thank you": "for", "i don't": "know"},
            "ru": {"привет": "как", "привет как": "дела", "спасибо за": "помощь", "я не": "могу"},
        }
        for lang, pairs in examples.items():
            with gzip.open(WORDS / f"{lang}-next.json.gz", "rt", encoding="utf-8") as stream:
                tables = json.load(stream)
            self.assertEqual(tables["format"], 1)
            for context, expected in pairs.items():
                with self.subTest(lang=lang, context=context):
                    table = tables["trigram" if " " in context else "bigram"]
                    self.assertIn(expected, table[context][:3])


if __name__ == "__main__":
    unittest.main()
