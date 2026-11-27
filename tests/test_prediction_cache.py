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

    def test_only_alphabet_and_period_keys_gain_the_alternates_menu(self):
        with tempfile.TemporaryDirectory() as directory:
            keyboard = self.load_keyboard(directory)
            layout = '''import QtQuick
KeyboardLayout {
    KeyboardRow {
        Key {
            id: referenceKey
            text: "е"
            alternativeKeys: "её"
        }
        Key {
            text: "."
            alternativeKeys: "!.?"
            smallText: "!?"
            smallTextVisible: true
        }
        Key {
            text: ","
            smallText: "⚙"
            smallTextVisible: keyboard.isFunctionPopupListAvailable()
        }
        BackspaceKey {
        }
    }
}'''
            result = keyboard.add_alternate_keys(layout)
            self.assertEqual(result.count("DeckKeyHint {"), 2)
            self.assertEqual(result.count("smallTextVisible: false"), 2)
            self.assertIn('Alternates.keys(text, "её")', result)
            self.assertIn('Alternates.keys(text, "!.?")', result)
            self.assertIn("id: referenceKey", result)
            self.assertIn("smallTextVisible: keyboard.isFunctionPopupListAvailable()", result)

    def test_morphology_cache_and_language_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            keyboard = self.load_keyboard(directory)
            source, index = root / "ru-morph.json.gz", root / "DeckMorphData.js"
            def build(families, stamp):
                source.write_bytes(gzip.compress(json.dumps({"format": 1, "families": families}).encode()))
                os.utime(source, ns=(stamp, stamp))
                keyboard.write_morphology(index, source)
                payload = index.read_text().split("JSON.parse(", 1)[1].rstrip().removesuffix(")")
                return json.loads(json.loads(payload))

            forms = [["графа", "NOUN", [["графы", 2, 2, 1], ["графы", 1, 0, 2]]],
                     ["граф", "NOUN", [["графы", 1, 0, 2]]]]
            self.assertEqual(build(forms, 2_000_000_000)["byWord"]["графы"], [0, 1])
            with patch.object(gzip, "open", side_effect=AssertionError("cache missed")):
                keyboard.write_morphology(index, source)
            self.assertEqual(build(forms[:1], 1_000_000_000)["byWord"]["графы"], [0])
            source.write_bytes(b"broken")
            with contextlib.redirect_stderr(io.StringIO()):
                keyboard.write_morphology(index, source)
            self.assertIn("var model = null", index.read_text())
            source.unlink()
            keyboard.write_morphology(index, None)  # English and fallback layouts
            self.assertIn("var model = null", index.read_text())

    def test_replacing_dictionary_with_older_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            keyboard = self.load_keyboard(directory)
            source, index = root / "ru.txt.gz", root / "DeckWords.js"
            for stamp, words in [(2_000_000_000, ["слово"]), (1_000_000_000, ["слово", "сленг"])]:
                source.write_bytes(gzip.compress(("\n".join(words) + "\n").encode()))
                os.utime(source, ns=(stamp, stamp))
                keyboard.write_completions(index, source)
                self.assertEqual(json.loads(index.read_text().split(" = ", 1)[1]), {"с": words, "сл": words})
            with patch.object(gzip, "open", side_effect=AssertionError("cache was not reused")):
                keyboard.write_completions(index, source)

    def test_legacy_index_replaced_despite_unchanged_dictionary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            keyboard = self.load_keyboard(directory)
            dictionary, index = root / "en.txt.gz", root / "DeckWords.js"
            dictionary.write_bytes(gzip.compress(b"him\nhere\nhello\nhow\nhave\n"))
            index.write_text('.pragma library\nvar words = {"he":["here","hello"]}\n')
            stamp = dictionary.stat().st_mtime_ns
            os.utime(index, ns=(stamp, stamp))
            keyboard.write_completions(index, dictionary)
            words = json.loads(index.read_text().split(" = ", 1)[1])
            self.assertEqual(words["h"][:3], ["hello", "how", "have"])
            self.assertEqual(words["he"], ["here", "hello"])
            self.assertEqual(len(words["h"]), len(set(words["h"])))

    def test_add_spelling_index_to_previous_install_then_roll_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            keyboard = self.load_keyboard(directory)
            source, index, spelling = root / "ru.txt.gz", root / "DeckWords.js", root / "DeckSpelling.js"
            source.write_bytes(gzip.compress("дом\nда\nдомик\nслово\n".encode()))
            os.utime(source, ns=(2_000_000_000, 2_000_000_000))
            keyboard.write_completions(index, source)
            # The old prefix cache is already up to date. The new spelling cache must be built.
            keyboard.write_completions(index, source, spelling)
            self.assertEqual(json.loads(spelling.read_text().split(" = ", 1)[1]),
                             {"до": {"м": 1, "мик": 3}, "да": {"": 2}, "сл": {"ово": 4}})
            with patch.object(gzip, "open", side_effect=AssertionError("reopened dictionary")):
                keyboard.write_completions(index, source, spelling)

            source.write_bytes(gzip.compress("домик\nдом\n".encode()))
            os.utime(source, ns=(1_000_000_000, 1_000_000_000))
            keyboard.write_completions(index, source, spelling)
            self.assertEqual(json.loads(spelling.read_text().split(" = ", 1)[1]),
                             {"до": {"мик": 1, "м": 2}})
            self.assertEqual(json.loads(index.read_text().split(" = ", 1)[1]), {"д": ["домик", "дом"], "до": ["домик", "дом"]})

    def test_replacement_and_missing_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            keyboard = self.load_keyboard(directory)
            model, index = root / "ru-next.json.gz", root / "DeckNext.js"

            def generate(word, stamp, suffix=None):
                data = {"format": 1, "bigram": {"доброе": [word]}, "trigram": {}}
                if suffix is not None:
                    data["suffix"] = suffix
                model.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False).encode()))
                os.utime(model, ns=(stamp, stamp))
                keyboard.write_predictions(index, model)

            # Old installations can have a valid timestamp and no suffix export.
            index.write_text(".pragma library\nvar bigram = {}\nvar trigram = {}\n")
            os.utime(index, ns=(2_000_000_000, 2_000_000_000))
            generate("утро", 2_000_000_000)
            self.assertIn('"доброе":["утро"]', index.read_text())
            self.assertIn("var suffix = {}", index.read_text())
            generate("дело", 1_000_000_000, suffix={"ого": ["дня"]})
            self.assertIn('var suffix = {"ого":["дня"]}', index.read_text())
            self.assertIn('"доброе":["дело"]', index.read_text())
            self.assertNotIn("утро", index.read_text())
            with patch.object(gzip, "open", side_effect=AssertionError("cache was not reused")):
                keyboard.write_predictions(index, model)
            model.unlink()
            with contextlib.redirect_stderr(io.StringIO()):
                keyboard.write_predictions(index, model)
            self.assertIn("var bigram = {}", index.read_text())
            self.assertIn("var trigram = {}", index.read_text())
            self.assertIn("var suffix = {}", index.read_text())
