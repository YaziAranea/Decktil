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


def count_sentences(sentences, lang):
    counts = Counter()
    context_documents = Counter()
    seen = set()
    for sentence in sentences:
        if sentence in seen:
            continue
        seen.add(sentence)
        edges = set(transitions(sentence, lang))
        counts.update(edges)
        context_documents.update({context for context, word in edges})
    return counts, context_documents


def rare_continuations(counts, context_documents):
    # Allow a single attested phrase to fill a context with no recurring
    # continuation. Both adjacent pairs of a three-word phrase must recur.
    following = Counter()
    recurring_contexts = set()
    for (context, word), count in counts.items():
        if " " not in context:
            following[word] += count
        if count >= 2:
            recurring_contexts.add(context)
    candidates = defaultdict(dict)
    for context, word in counts:
        if context in recurring_contexts or context_documents[context] < 2:
            continue
        if " " in context:
            left, middle = context.split()
            if counts[left, middle] < 2 or counts[middle, word] < 2:
                continue
            support = counts[middle, word]
        else:
            support = following[word]
            if support < 8:
                continue
        candidates[context][word] = support
    return {(context, word): words[word]
            for context, words in candidates.items()
            for word in sorted(words, key=lambda w: (-words[w], w))[:CANDIDATES]}


def count_export(lang, archive):
    iso, checksum = SOURCES[lang]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
        raise ValueError(f"{archive.name} is not the documented CC0 snapshot")

    def sentences(rows):
        for row in rows:
            _, language, sentence, _ = row.rstrip("\n").split("\t", 3)
            if language != iso:
                raise ValueError(f"Unexpected language in {archive.name}")
            yield sentence

    with bz2.open(archive, "rt", encoding="utf-8") as rows:
        counts, documents = count_sentences(sentences(rows), lang)
    recurring = {edge: n for edge, n in counts.items() if n >= 2}
    return recurring, rare_continuations(counts, documents)


def snapshot_text(counts):
    return "".join(f"{context}\t{word}\t{count}\n"
                   for (context, word), count in sorted(counts.items()))


def read_counts(path):
    table = defaultdict(Counter)
    with gzip.open(path, "rt", encoding="utf-8") as rows:
        for row in rows:
            context, word, count = row.rstrip("\n").split("\t")
            table[context][word] = int(count)
    return table


def suffix_model(counts):
    # One vote per different predecessor spelling, independent of frequency.
    votes = defaultdict(Counter)
    contexts = Counter()
    for previous, followers in counts.items():
        if not previous.isalpha():
            continue
        for size in range(2, min(5, len(previous))):
            suffix = previous[-size:]
            contexts[suffix] += 1
            votes[suffix].update(followers.keys())
    table = {}
    for suffix, followers in sorted(votes.items()):
        if contexts[suffix] < (6 if len(suffix) == 2 else 4):
            continue
        words = [word for word, count in followers.items() if count >= 3]
        if words:
            table[suffix] = sorted(words, key=lambda w: (-followers[w], w))[:CANDIDATES]
    return table


def rank_model(lang, training):
    counts = read_counts(training / f"{lang}-counts.tsv.gz")
    suffix = suffix_model(counts)
    for sentence in (training / f"{lang}-chat.txt").read_text(encoding="utf-8").splitlines():
        for context, word in set(transitions(sentence, lang)):
            counts[context][word] += SEED_WEIGHT
    # Project examples and independently recurring transitions always take
    # precedence. Weak evidence only supplies previously uncovered contexts.
    for context, words in read_counts(training / f"{lang}-backoff.tsv.gz").items():
        if context not in counts:
            counts[context] = words
    result = {"format": 1, "bigram": {}, "trigram": {}, "suffix": suffix}
    for context, followers in sorted(counts.items()):
        name = "trigram" if " " in context else "bigram"
        result[name][context] = sorted(followers, key=lambda w: (-followers[w], w))[:CANDIDATES]
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
            recurring, rare = count_export(lang, archive)
            write_gzip(args.training_dir / f"{lang}-counts.tsv.gz", snapshot_text(recurring))
            write_gzip(args.training_dir / f"{lang}-backoff.tsv.gz", snapshot_text(rare))
        model = rank_model(lang, args.training_dir)
        target = args.output_dir / f"{lang}-next.json.gz"
        write_gzip(target, json.dumps(model, ensure_ascii=False, separators=(",", ":")))
        print(f"{lang}: {len(model['bigram'])} one-word and "
              f"{len(model['trigram'])} two-word contexts, {len(model['suffix'])} endings, "
              f"{target.stat().st_size} bytes")


if __name__ == "__main__":
    main()
