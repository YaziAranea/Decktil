# Word list attribution

The English and Russian completion lists are adapted from Hermit Dave’s **FrequencyWords**, based on the 2018 OpenSubtitles corpus. The upstream lists and these adapted word lists are licensed under [Creative Commons Attribution–ShareAlike 4.0 International](https://creativecommons.org/licenses/by-sa/4.0/).

Each dictionary starts with the first 100,000 distinct eligible entries in the full frequency list for its language. The generator keeps lowercase words between 2 and 32 characters, including internal hyphens and English apostrophes, normalizes curly apostrophes and removes frequency counts. It preserves the first 3,000 entries, then interleaves one curated chat or gaming term per eight corpus words. Duplicate entries retain their best position. The original Decktil slang selections in `training/en-slang.txt` and `training/ru-slang.txt` are available under the [project’s MIT license](../../LICENSE). The combined completion dictionaries are distributed under CC BY-SA 4.0. [Upstream license](https://github.com/hermitdave/FrequencyWords/blob/525f9b560de45753a5ea01069454e72e9aa541c6/README.md#license) · [OpenSubtitles corpus](https://opus.nlpl.eu/OpenSubtitles.php)

To rebuild the completion dictionaries, run `python3 tools/build_completions.py` in the repository root. It downloads the pinned source revision and checks the SHA-256 digests below. If the source files are already on disk, pass `--source-dir /path/to/files` to rebuild offline. The installed keyboard reads the bundled dictionaries and needs no download.

| FrequencyWords input | SHA-256 |
| --- | --- |
| [en_full.txt](https://raw.githubusercontent.com/hermitdave/FrequencyWords/525f9b560de45753a5ea01069454e72e9aa541c6/content/2018/en/en_full.txt) | `7fea67ab954e2c01df6c608c9826e594cf36f8823b3243554f88245fb75dc506` |
| [ru_full.txt](https://raw.githubusercontent.com/hermitdave/FrequencyWords/525f9b560de45753a5ea01069454e72e9aa541c6/content/2018/ru/ru_full.txt) | `32dfd94138aea2667ec455dcc8512029e39bd4b8ea70b09410d569b69d511a30` |

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
