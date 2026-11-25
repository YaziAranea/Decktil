#!/usr/bin/env python3
"""Rebuild Decktil's local next-word models from the bundled anonymous counts."""

import argparse
import bz2
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import re
import unicodedata


WORDS = Path(__file__).resolve().parents[1] / "src" / "words"
TRAINING = WORDS / "training"
SOURCES = {
    "en": ("eng", "c2e2edd063a31cbbc8e416a4858225e934bbb0afff485031821e59f230ccf1ca"),
    "ru": ("rus", "fb9e878c3d673c7ec5d7ec64d3ec0ae80a7df89f846f41e150144ba4bee4971f"),
}
WORD = {
    "en": re.compile(r"[a-z]+(?:'[a-z]+)*\Z", re.ASCII | re.IGNORECASE),
    "ru": re.compile(r"[а-яё]+(?:-[а-яё]+)*\Z", re.IGNORECASE),
}
# Keep foreign tokens and punctuation visible so they interrupt the context.
TOKENS = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*|[^\s]", re.UNICODE)
SEED_WEIGHT = 18
CONTEXT_LIMIT = {1: 8000, 2: 16500}
CANDIDATES = 5


def transitions(sentence, lang):
    previous = []
    sentence = unicodedata.normalize("NFC", sentence).replace("’", "'")
    if "://" in sentence or "@" in sentence:
        return
    for match in TOKENS.finditer(sentence):
        token = match.group()
        lower = token.lower()
        if token == ",":
            continue
        if (len(token) > 32 or not WORD[lang].fullmatch(token)
                or (previous and token[0].isupper() and lower != "i")):
            previous.clear()
            continue
        if previous:
            yield previous[-1], lower
        if len(previous) == 2:
            yield " ".join(previous), lower
        previous = [*previous[-1:], lower]


def write_gzip(path, data):
    with path.open("wb") as target:
        with gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as output:
            output.write(data.encode("utf-8"))


def count_export(lang, archive):
    iso, checksum = SOURCES[lang]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
        raise ValueError(f"{archive.name} is not the documented CC0 snapshot")
    counts = Counter()
    seen = set()
    with bz2.open(archive, "rt", encoding="utf-8") as sentences:
        for row in sentences:
            _, language, sentence, _ = row.rstrip("\n").split("\t", 3)
            if language != iso:
                raise ValueError(f"Unexpected language in {archive.name}")
            if sentence not in seen:
                seen.add(sentence)
                counts.update(set(transitions(sentence, lang)))
    # Keep only independently recurring sequences. No sentence or attribution
    # fields are written to the intermediate snapshot or shipped model.
    return "".join(f"{context}\t{word}\t{count}\n"
                   for (context, word), count in sorted(counts.items()) if count >= 2)


def rank_model(lang, training):
    counts = defaultdict(Counter)
    with gzip.open(training / f"{lang}-counts.tsv.gz", "rt", encoding="utf-8") as rows:
        for row in rows:
            context, word, count = row.rstrip("\n").split("\t")
            counts[context][word] = int(count)
    for sentence in (training / f"{lang}-chat.txt").read_text(encoding="utf-8").splitlines():
        for context, word in set(transitions(sentence, lang)):
            counts[context][word] += SEED_WEIGHT
    result = {"format": 1}
    for size, name in ((1, "bigram"), (2, "trigram")):
        eligible = [context for context in counts if len(context.split()) == size]
        eligible.sort(key=lambda context: (-sum(counts[context].values()), context))
        table = {}
        for context in sorted(eligible[:CONTEXT_LIMIT[size]]):
            words = sorted(counts[context], key=lambda w: (-counts[context][w], w))
            table[context] = words[:CANDIDATES]
        result[name] = table
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=WORDS)
    parser.add_argument("--training-dir", type=Path, default=TRAINING)
    parser.add_argument("--from-exports", type=Path, metavar="DIRECTORY",
                        help="regenerate anonymous count snapshots from the two pinned Tatoeba CC0 archives")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for lang, (iso, _) in SOURCES.items():
        if args.from_exports:
            archive = args.from_exports / f"{iso}_sentences_CC0.tsv.bz2"
            snapshot = count_export(lang, archive)
            write_gzip(args.training_dir / f"{lang}-counts.tsv.gz", snapshot)
        model = rank_model(lang, args.training_dir)
        target = args.output_dir / f"{lang}-next.json.gz"
        write_gzip(target, json.dumps(model, ensure_ascii=False, separators=(",", ":")))
        print(f"{lang}: {len(model['bigram'])} one-word and "
              f"{len(model['trigram'])} two-word contexts, {target.stat().st_size} bytes")


if __name__ == "__main__":
    main()
