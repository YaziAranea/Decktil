#!/usr/bin/env python3
"""Rebuild the RU and EN completion dictionaries from pinned FrequencyWords data."""

import argparse
import gzip
import hashlib
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from urllib.request import urlopen


WORDS = Path(__file__).resolve().parents[1] / "src" / "words"
REVISION = "525f9b560de45753a5ea01069454e72e9aa541c6"
SOURCE = f"https://raw.githubusercontent.com/hermitdave/FrequencyWords/{REVISION}/content/2018"
CHECKSUM = {
    "en": "7fea67ab954e2c01df6c608c9826e594cf36f8823b3243554f88245fb75dc506",
    "ru": "32dfd94138aea2667ec455dcc8512029e39bd4b8ea70b09410d569b69d511a30",
}
TOKEN = {
    "en": re.compile(r"[a-z]+(?:['-][a-z]+)*"),
    "ru": re.compile(r"[а-яё]+(?:-[а-яё]+)*"),
}
CORPUS_LIMIT = 100_000
COMMON_WORDS = 3_000
SLANG_INTERVAL = 8


def corpus_words(path, lang):
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != CHECKSUM[lang]:
        raise ValueError(f"Unexpected SHA-256 for {path.name}: {digest}")

    words = {}
    with path.open(encoding="utf-8") as source:
        for line in source:
            word, _ = line.rstrip("\n").rsplit(" ", 1)
            word = word.replace("’", "'")
            if 2 <= len(word) <= 32 and TOKEN[lang].fullmatch(word):
                words[word] = None
                if len(words) == CORPUS_LIMIT:
                    break
    if len(words) != CORPUS_LIMIT:
        raise ValueError(f"{path.name} contains only {len(words)} eligible words")
    return list(words)


def read_slang(lang):
    path = WORDS / "training" / f"{lang}-slang.txt"
    words = path.read_text(encoding="utf-8").splitlines()
    for line, word in enumerate(words, 1):
        if not (2 <= len(word) <= 32 and TOKEN[lang].fullmatch(word)):
            raise ValueError(f"{path.name}:{line}: invalid completion {word!r}")
    if len(words) != len(set(words)):
        raise ValueError(f"Repeated completion in {path.name}")
    return words


def rank_words(corpus, slang):
    # Keep common completions first. Introduce one curated word for every
    # eight corpus entries, preserving higher-ranked occurrences of the same word.
    slots = {COMMON_WORDS + i * SLANG_INTERVAL: word for i, word in enumerate(slang)}
    ranked = {}
    for i, word in enumerate(corpus):
        if i in slots:
            ranked[slots[i]] = None
        ranked[word] = None
    ranked.update(dict.fromkeys(slang))
    return list(ranked)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, metavar="DIR",
                        help="read en_full.txt and ru_full.txt here instead of downloading")
    parser.add_argument("--output-dir", type=Path, default=WORDS)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output_dir) as temp:
        stage = Path(temp)
        for lang in CHECKSUM:
            name = f"{lang}_full.txt"
            source = (args.source_dir or stage) / name
            if args.source_dir is None:
                with urlopen(f"{SOURCE}/{lang}/{name}", timeout=120) as remote, source.open("wb") as copy:
                    shutil.copyfileobj(remote, copy)
            words = rank_words(corpus_words(source, lang), read_slang(lang))
            with (stage / f"{lang}.txt.gz").open("wb") as file:
                with gzip.GzipFile(filename="", fileobj=file, mode="wb", mtime=0, compresslevel=9) as out:
                    out.write(("\n".join(words) + "\n").encode("utf-8"))
            print(f"{lang}: {len(words)} completions")
        for lang in CHECKSUM:
            name = f"{lang}.txt.gz"
            (stage / name).replace(args.output_dir / name)


if __name__ == "__main__":
    main()
