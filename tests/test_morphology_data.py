import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_morphology as builder


class MorphologyDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with gzip.open(ROOT / "src" / "words" / "ru-morph.json.gz", "rt", encoding="utf-8") as source:
            table = json.load(source)
        if table["format"] != 1:
            raise AssertionError("Unsupported inflection table format")
        cls.families = table["families"]
        cls.companions = table["companions"]

    def test_dictionary_preserves_ambiguous_cases_and_numbers(self):
        examples = [
            ("книга", "NOUN", ["книги", 2, "f", "s"]),
            ("книга", "NOUN", ["книги", 9, "f", "p"]),
            ("игра", "NOUN", ["игре", 36, "f", "s"]),
            ("встреча", "NOUN", ["встречей", 16, "f", "s"]),
            ("окно", "NOUN", ["окно", 9, "n", "s"]),
            ("новый", "ADJF", ["новой", 54, "f", "s"]),
            ("новый", "ADJF", ["новые", 9, "", "p"]),
            ("густой", "ADJF", ["густом", 32, "m", "s"]),
            ("густой", "ADJF", ["густом", 32, "n", "s"]),
            ("она", "NPRO", ["неё", 10, "f", "s"]),
            ("два", "NUMR", ["двумя", 16, "", ""]),
            ("лес", "NOUN", ["лесу", 4 | 64 | 128, "m", "s"]),
            ("лес", "NOUN", ["лесе", 32, "m", "s"]),
            ("чай", "NOUN", ["чаю", 4 | 64 | 128, "m", "s"]),
            ("чай", "NOUN", ["чае", 32, "m", "s"]),
            ("шкаф", "NOUN", ["шкафу", 4 | 64, "m", "s"]),
            ("шкаф", "NOUN", ["шкафе", 32, "m", "s"]),
        ]
        for lemma, part, expected in examples:
            with self.subTest(lemma=lemma, expected=expected):
                self.assertTrue(any(expected in forms for word, pos, forms in self.families
                                    if (word, pos) == (lemma, part)))

    def test_rare_non_adjective_families_only_contain_attested_forms(self):
        with gzip.open(ROOT / "src" / "words" / "ru.txt.gz", "rt", encoding="utf-8") as source:
            vocabulary = set(source.read().splitlines())
        self.assertGreater(len(self.families), builder.FULL_FAMILIES)
        for rank, (lemma, part, forms) in enumerate(self.families):
            self.assertTrue(lemma and forms)
            self.assertIn(part, builder.PARTS)
            readings = set()
            for word, mask, gender, number in forms:
                self.assertTrue(0 < mask < 256)
                self.assertIn(gender, ("m", "f", "n", ""))
                self.assertIn(number, ("s", "p", ""))
                self.assertNotIn((word, gender, number), readings)
                readings.add((word, gender, number))
                if rank >= builder.FULL_FAMILIES and part != "ADJF":
                    self.assertIn(word, vocabulary)

    def test_observed_companions_use_unique_dictionary_lemmas(self):
        adjectives = {word for word, part, _ in self.families if part == "ADJF"}
        nouns = {word for word, part, _ in self.families if part == "NOUN"}
        for adj, candidates in self.companions.items():
            self.assertIn(adj, adjectives)
            self.assertTrue(0 < len(candidates) <= builder.MAX_COMPANIONS)
            self.assertEqual(len(candidates), len(set(candidates)))
            self.assertTrue(set(candidates) <= nouns)
        for adj, noun in [("важный", "встреча"), ("горячий", "чай"), ("горячий", "вода"),
                         ("хороший", "идея"), ("последний", "раз")]:
            self.assertIn(noun, self.companions[adj])
        self.assertNotIn("человек", self.companions.get("выходной", ()))
        self.assertNotIn("мой", self.companions)

    def test_deterministic_encoding_and_wheel_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            data = [["окно", "NOUN", [["окно", 9, "n", "s"]]]]
            first, second = temp / "first.gz", temp / "second.gz"
            builder.write_model(first, data)
            builder.write_model(second, data)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with gzip.open(first, "rt", encoding="utf-8") as source:
                self.assertEqual(json.load(source), {"format": 1, "families": data})
            filename = builder.WHEELS[0][0].rsplit("/", 1)[1]
            (temp / filename).write_bytes(b"incorrect wheel")
            with self.assertRaisesRegex(ValueError, "Unexpected SHA-256"):
                builder.load_dictionary(temp, temp)
