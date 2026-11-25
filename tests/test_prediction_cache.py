"""Regenerating a keyboard layout must use the model from the current installation."""
import contextlib
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class PredictionCacheTests(unittest.TestCase):
    def load_keyboard(self, directory):
        source = Path(__file__).resolve().parents[1] / "src" / "keyboard.py"
        spec = importlib.util.spec_from_file_location("decktil_layout", source)
        keyboard = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, XDG_RUNTIME_DIR=directory):
            spec.loader.exec_module(keyboard)
        return keyboard

    def test_replacing_dictionary_with_older_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            keyboard = self.load_keyboard(directory)
            source, index = root / "ru.txt.gz", root / "DeckWords.js"
            for stamp, words in [(2_000_000_000, ["слово"]), (1_000_000_000, ["слово", "сленг"])]:
                source.write_bytes(gzip.compress(("\n".join(words) + "\n").encode()))
                os.utime(source, ns=(stamp, stamp))
                keyboard.write_completions(index, source)
                self.assertEqual(json.loads(index.read_text().split(" = ", 1)[1]), {"сл": words})
            with patch.object(gzip, "open", side_effect=AssertionError("cache was not reused")):
                keyboard.write_completions(index, source)

    def test_replacement_and_missing_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            keyboard = self.load_keyboard(directory)
            model, index = root / "ru-next.json.gz", root / "DeckNext.js"

            def generate(word, stamp):
                data = {"format": 1, "bigram": {"доброе": [word]}, "trigram": {}}
                model.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False).encode()))
                os.utime(model, ns=(stamp, stamp))
                keyboard.write_predictions(index, model)

            generate("утро", 2_000_000_000)
            self.assertIn('"доброе":["утро"]', index.read_text())
            generate("дело", 1_000_000_000)
            self.assertIn('"доброе":["дело"]', index.read_text())
            self.assertNotIn("утро", index.read_text())
            with patch.object(gzip, "open", side_effect=AssertionError("cache was not reused")):
                keyboard.write_predictions(index, model)
            model.unlink()
            with contextlib.redirect_stderr(io.StringIO()):
                keyboard.write_predictions(index, model)
            self.assertIn("var bigram = {}", index.read_text())
            self.assertIn("var trigram = {}", index.read_text())
