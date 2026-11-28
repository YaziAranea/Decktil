#!/usr/bin/env python3
"""Build Decktil's Russian inflection table from pinned OpenCorpora data."""

import argparse
import bz2
from collections import Counter, defaultdict
from functools import lru_cache
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.request import urlopen
import zipfile

from build_predictions import SOURCES, transitions


WORDS = Path(__file__).resolve().parents[1] / "src" / "words"
WHEELS = (
    (
        "https://files.pythonhosted.org/packages/18/4b/59bac03278033e293d1405ed42fb6c6252c25f40c50f509c615caeaa3b71/pymorphy3-2.0.6-py3-none-any.whl",
        "0254317c02ce3ea17e080b7fc9d675e44662b3a5296bae68605b7a41d25b36c3",
    ),
    (
        "https://files.pythonhosted.org/packages/b0/67/469e9e52d046863f5959928794d3067d455a77f580bf4a662630a43eb426/pymorphy3_dicts_ru-2.4.417150.4580142-py2.py3-none-any.whl",
        "718bac64c73c10c16073a199402657283d9b64c04188b694f6d3e9b0d85440f4",
    ),
    (
        "https://files.pythonhosted.org/packages/84/3b/7fb4c1a8df59cb80f5f7ecb9646280e000f9ba2ccff8710205dc9aa4604f/dawg2_python-0.9.0-py3-none-any.whl",
        "4fab6fc097bd176cd783cd8421b757348ea5a460789e53b0f6bb64831380bab5",
    ),
)

CASES = {"nomn": 1, "gent": 2, "gen1": 2, "gen2": 128, "datv": 4, "accs": 8,
         "acc2": 8, "ablt": 16, "loct": 32, "loc1": 32, "loc2": 64}
GENDERS = {"masc": "m", "femn": "f", "neut": "n"}
NUMBERS = {"sing": "s", "plur": "p"}
PARTS = {"NOUN", "ADJF", "NPRO", "NUMR"}
EXCLUDED = {"Name", "Patr", "Surn", "Geox", "Orgn", "Trad", "Abbr", "Erro", "Dist", "Arch"}
FULL_FAMILIES = 2500
MAX_COMPANIONS = 8
RARE_COMPANIONS = "ru-rare-companions.tsv.gz"


def load_dictionary(target, wheel_dir):
    for url, expected in WHEELS:
        name = url.rsplit("/", 1)[1]
        if wheel_dir:
            data = (wheel_dir / name).read_bytes()
        else:
            with urlopen(url, timeout=60) as response:
                data = response.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Unexpected SHA-256 for {name}")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(target)

    sys.path.insert(0, str(target))
    try:
        import pymorphy3_dicts_ru
        from pymorphy3.analyzer import ProbabilityEstimator
        from pymorphy3.opencorpora_dict.wrapper import Dictionary
        path = pymorphy3_dicts_ru.get_path()
        return Dictionary(path), ProbabilityEstimator(path).p_t_given_w
    finally:
        sys.path.pop(0)


def features(tag):
    if tag.POS in PARTS and tag.case in CASES and EXCLUDED.isdisjoint(tag.grammemes):
        return (str(tag.POS), CASES[tag.case], GENDERS.get(tag.gender, ""),
                NUMBERS.get(tag.number, ""))
    return None


def families(dictionary, vocabulary, full_families=FULL_FAMILIES):
    """Separate homonyms by dictionary stem and paradigm, rank by first attested form."""
    ranks = {word: rank for rank, word in enumerate(dict.fromkeys(vocabulary))}
    tagged = {tag: features(tag) for tag in dictionary.gramtab}
    found = {}
    for word, rank in ranks.items():
        for paradigm_id, form_id in dictionary.words.get(word, ()):
            reading = tagged[dictionary.build_tag_info(paradigm_id, form_id)]
            if reading is None:
                continue
            paradigm = dictionary.paradigms[paradigm_id]
            stem = dictionary.build_stem(paradigm, form_id, word)
            key = (paradigm_id, stem, reading[0])
            if key not in found:
                lemma = dictionary.build_normal_form(paradigm_id, form_id, word)
                found[key] = (rank, lemma)

    paradigms = {}
    output = []
    for key, (_, lemma) in sorted(found.items(), key=lambda item: (item[1], item[0])):
        paradigm_id, stem, part = key
        if paradigm_id not in paradigms:
            paradigms[paradigm_id] = [
                (prefix, tagged[tag], suffix)
                for prefix, tag, suffix in dictionary.build_paradigm_info(paradigm_id)
                if tagged[tag] is not None
            ]
        forms = {}
        for prefix, (form_part, case, gender, number), suffix in paradigms[paradigm_id]:
            word = prefix + stem + suffix
            if form_part != part or (len(output) >= full_families and part != "ADJF" and word not in ranks):
                continue
            form = (word, gender, number)
            forms[form] = forms.get(form, 0) | case
        entries = [[word, mask, gender, number] for (word, gender, number), mask in forms.items()]
        output.append([lemma, part, entries])
    return output


def technical_nouns(training):
    """Read hand-reviewed, inanimate masculine paradigms in explicit case order."""
    # Nominative and accusative coincide in both numbers for these nouns.
    nominative = CASES["nomn"] | CASES["accs"]
    readings = [(nominative, "s"), (nominative, "p")]
    for number in ("s", "p"):
        readings.extend((CASES[case], number) for case in ("gent", "datv", "ablt", "loct"))
    path = training / "ru-tech-nouns.tsv"
    seen = set()
    for line, row in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        forms = row.split("\t")
        if len(forms) != len(readings) or not all(re.fullmatch(r"[а-яё]+(?:-[а-яё]+)*", w)
                                                   and 2 <= len(w) <= 32 for w in forms):
            raise ValueError(f"{path.name}:{line}: invalid technical noun paradigm")
        lemma = forms[0]
        if lemma in seen:
            raise ValueError(f"{path.name}:{line}: repeated lemma {lemma}")
        seen.add(lemma)
        yield [lemma, "NOUN", [[word, mask, "m", number]
                               for word, (mask, number) in zip(forms, readings)]]


def pair_resolver(dictionary, probabilities, data):
    known = {(part, lemma) for lemma, part, _ in data}

    @lru_cache(maxsize=None)
    def readings(word, part):
        records = set()
        parses = [(dictionary.build_tag_info(paradigm, form), paradigm, form)
                  for paradigm, form in dictionary.words.get(word, ())]
        tags = {tag for tag, _, _ in parses}
        parts = {tag.POS for tag in tags}
        if part not in parts:
            return ()
        if len(parts) > 1:
            # Avoid treating a rare noun reading of a common verb or particle as evidence.
            probability = math.fsum(probabilities.prob(word, tag) for tag in tags if tag.POS == part)
            comparable = (part == "ADJF" and parts <= {"ADJF", "COMP", "ADJS", "PRTF", "PRTS"}
                          and not any(probabilities.prob(word, tag) for tag in tags))
            if probability < 0.5 and not comparable:
                return ()
        for tag, paradigm, form in parses:
            if tag.POS != part or "Apro" in tag.grammemes:
                continue
            reading = features(tag)
            if reading is None:
                continue
            lemma = dictionary.build_normal_form(paradigm, form, word)
            if (part, lemma) in known:
                records.add((lemma, *reading[1:]))
        return records

    def resolve(left, right):
        pairs = set()
        for adj, adj_case, adj_gender, adj_number in readings(left, "ADJF"):
            for noun, noun_case, noun_gender, noun_number in readings(right, "NOUN"):
                agreement_case = {64: 32, 128: 2}.get(noun_case, noun_case)
                if (adj_case & agreement_case and adj_number == noun_number
                        and (adj_number == "p" or adj_gender == noun_gender)):
                    pairs.add((adj, noun))
        if len(pairs) == 1:
            return pairs.pop()
        return None

    return resolve


def recurring_bigrams(training):
    with gzip.open(training / "ru-counts.tsv.gz", "rt", encoding="utf-8") as source:
        for line in source:
            left, right, count = line.rstrip("\n").split("\t")
            if " " not in left:
                yield (left, right), int(count)


def rare_companions(archive, recurring, resolve):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCES["ru"][1]:
        raise ValueError("Unexpected SHA-256 for the Russian Tatoeba CC0 export")
    totals = Counter()
    seen = set()
    with bz2.open(archive, "rt", encoding="utf-8") as sentences:
        for line in sentences:
            _, lang, sentence, _ = line.rstrip("\n").split("\t", 3)
            if lang != "rus":
                raise ValueError("Unexpected language in Russian Tatoeba CC0 export")
            if sentence in seen:
                continue
            seen.add(sentence)
            for left, right in set(transitions(sentence, "ru")):
                if " " in left or (left, right) in recurring:
                    continue
                lemmas = resolve(left, right)
                if lemmas:
                    totals[lemmas] += 1
    return "".join(f"{adj}\t{noun}\t{count}\n" for (adj, noun), count in sorted(totals.items()))


def companions(data, resolve, recurring, training):
    counts = defaultdict(Counter)
    for (left, right), count in recurring.items():
        pair = resolve(left, right)
        if pair:
            counts[pair[0]][pair[1]] += count
    with gzip.open(training / RARE_COMPANIONS, "rt", encoding="utf-8") as rows:
        for row in rows:
            adj, noun, count = row.rstrip("\n").split("\t")
            counts[adj][noun] += int(count)
    for sentence in set((training / "ru-chat.txt").read_text(encoding="utf-8").splitlines()):
        for left, right in set(transitions(sentence, "ru")):
            if " " not in left:
                pair = resolve(left, right)
                if pair:
                    counts[pair[0]][pair[1]] += 1

    # The snapshot may contain lemmas omitted by a custom --source vocabulary.
    nouns = {}
    adjectives = set()
    for rank, (lemma, part, _) in enumerate(data):
        if part == "NOUN":
            nouns.setdefault(lemma, rank)
        if part == "ADJF":
            adjectives.add(lemma)
    result = {}
    for adj in sorted(adjectives.intersection(counts)):
        candidates = counts[adj].keys() & nouns.keys()
        if candidates:
            ranked = sorted(candidates, key=lambda noun: (-counts[adj][noun], nouns[noun], noun))
            result[adj] = ranked[:MAX_COMPANIONS]
    return result


def write_gzip(path, payload):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        tmp = Path(temporary.name)
    try:
        with tmp.open("wb") as stream:
            with gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0, compresslevel=9) as zipped:
                zipped.write(payload.encode("utf-8"))
        tmp.chmod(0o644)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_model(path, data, pairs=None):
    model = {"format": 1, "families": data}
    if pairs:
        model["companions"] = pairs
    write_gzip(path, json.dumps(model, ensure_ascii=False, separators=(",", ":")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=WORDS / "ru.txt.gz")
    parser.add_argument("--output", type=Path, default=WORDS / "ru-morph.json.gz")
    parser.add_argument("--training-dir", type=Path, default=WORDS / "training")
    parser.add_argument("--from-export", type=Path, help="Rebuild the rare pair counts from the pinned CC0 export")
    parser.add_argument("--wheel-dir", type=Path, help="Read the three pinned wheels from this directory offline")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="decktil-morph-") as temp:
        dictionary, probabilities = load_dictionary(Path(temp), args.wheel_dir)
        with gzip.open(args.source, "rt", encoding="utf-8") as source:
            vocabulary = [line.strip() for line in source if line.strip()]
        data = families(dictionary, vocabulary)
        known_nouns = {lemma for lemma, part, _ in data if part == "NOUN"}
        available = set(vocabulary)
        for lemma, part, forms in technical_nouns(args.training_dir):
            if lemma in available and lemma not in known_nouns:
                data.append([lemma, part, [form for form in forms if form[0] in available]])
        resolve = pair_resolver(dictionary, probabilities, data)
        recurring = dict(recurring_bigrams(args.training_dir))
        if args.from_export:
            snapshot = rare_companions(args.from_export, recurring, resolve)
            write_gzip(args.training_dir / RARE_COMPANIONS, snapshot)
        pairs = companions(data, resolve, recurring, args.training_dir)
        write_model(args.output, data, pairs)
    print(f"{len(data):,} families, {sum(len(entry[2]) for entry in data):,} form records, "
          f"{sum(len(nouns) for nouns in pairs.values()):,} companion pairs, {args.output.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
