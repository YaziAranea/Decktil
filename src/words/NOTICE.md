# Word list attribution

The English and Russian completion lists are adapted from Hermit Dave’s **FrequencyWords**, based on the 2018 OpenSubtitles corpus. The upstream lists and these adapted word lists are licensed under [Creative Commons Attribution–ShareAlike 4.0 International](https://creativecommons.org/licenses/by-sa/4.0/).

The included files retain frequency order, omit frequency counts and keep the first 30,000 distinct words made of 2–32 letters for each language. The local keyboard groups entries by their first two letters. [English source list](https://github.com/hermitdave/FrequencyWords/blob/525f9b560de45753a5ea01069454e72e9aa541c6/content/2018/en/en_50k.txt) · [Russian source list](https://github.com/hermitdave/FrequencyWords/blob/525f9b560de45753a5ea01069454e72e9aa541c6/content/2018/ru/ru_50k.txt) · [Upstream license information](https://github.com/hermitdave/FrequencyWords/tree/525f9b560de45753a5ea01069454e72e9aa541c6#license) · [OpenSubtitles corpus](https://opus.nlpl.eu/OpenSubtitles.php)

## Next-word models

The next-word models in `en-next.json.gz` and `ru-next.json.gz` use English and Russian sentences from Tatoeba's separate **CC0** export. The 21 November 2026 snapshot contains 41,515 English rows and 23,109 Russian rows. The training script reads the published sentence text and drops the sentence ID and edit timestamp. [Tatoeba downloads and file descriptions](https://tatoeba.org/en/downloads) · [English CC0 archive](https://downloads.tatoeba.org/exports/per_language/eng/eng_sentences_CC0.tsv.bz2) · [Russian CC0 archive](https://downloads.tatoeba.org/exports/per_language/rus/rus_sentences_CC0.tsv.bz2) · [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/)

The aggregate count snapshots contain only transitions from Tatoeba’s CC0 export. The training directory also contains short conversation examples written for Decktil. These examples, the build script and Decktil’s contributions to the combined next-word tables are distributed under the [project’s MIT license](../../LICENSE).

The generator counts each transition once per distinct source sentence and keeps transitions seen in at least two sentences. It adds the project examples with a weight of 18 and keeps up to five ranked continuations per context. The bundled count snapshots allow the exact models to be rebuilt offline after Tatoeba rotates its downloads. Run from the repository root:

```sh
python3 tools/build_predictions.py
```

To regenerate the count snapshots, place the two original archives in one directory and run `python3 tools/build_predictions.py --from-exports /path/to/archives`. The generator checks their SHA-256 hashes.

| Archive | SHA-256 |
| --- | --- |
| `eng_sentences_CC0.tsv.bz2` | `c2e2edd063a31cbbc8e416a4858225e934bbb0afff485031821e59f230ccf1ca` |
| `rus_sentences_CC0.tsv.bz2` | `fb9e878c3d673c7ec5d7ec64d3ec0ae80a7df89f846f41e150144ba4bee4971f` |
