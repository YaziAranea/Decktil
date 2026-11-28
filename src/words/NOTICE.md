# Word list attribution

The English and Russian completion lists are adapted from Hermit Dave’s **FrequencyWords**, based on the 2018 OpenSubtitles corpus. The upstream lists and these adapted word lists are licensed under [Creative Commons Attribution–ShareAlike 4.0 International](https://creativecommons.org/licenses/by-sa/4.0/).

Each dictionary preserves the first 100,000 distinct eligible entries in its full frequency list. To extend coverage without admitting arbitrary subtitle spellings, it selects the next 30,000 words that occur at least twice and match an independent dictionary. English candidates must occur with the same lowercase spelling in the U.S. large word list from Kevin Atkinson’s English Speller Database (ESDB). Russian candidates must have an exact OpenCorpora reading that is not marked as a proper name, abbreviation, error, distortion or archaic usage. The OpenCorpora data and license are described under Russian morphology below. [ESDB project](https://wordlist.aspell.net/) · [ESDB source and copyright](https://github.com/en-wl/wordlist-diff/blob/7f2f4078354752045ee16a041605686561122185/Copyright)

The builder keeps lowercase words of 2 to 32 characters, accepts internal hyphens and English apostrophes, normalizes curly apostrophes and removes frequency counts. It preserves the first 3,000 entries, then interleaves one curated chat, gaming, technical or everyday term per eight corpus words. Duplicate entries retain their best position. The original Decktil selections in `training/en-slang.txt`, `training/ru-slang.txt` and `training/ru-tech-nouns.tsv` are available under the [project’s MIT license](../../LICENSE). The combined completion dictionaries are distributed under CC BY-SA 4.0. [FrequencyWords license](https://github.com/hermitdave/FrequencyWords/blob/525f9b560de45753a5ea01069454e72e9aa541c6/README.md#license) · [OpenSubtitles corpus](https://opus.nlpl.eu/OpenSubtitles.php)

Run `python3 tools/build_completions.py` in the repository root to rebuild the completion dictionaries. It verifies the pinned text files below and the three Russian dictionary wheels used by the morphology builder. For an offline rebuild, use `--source-dir /path/to/text-files --wheel-dir /path/to/wheels`. The installed keyboard reads the bundled dictionaries offline.

| Text input | SHA-256 |
| --- | --- |
| [en_full.txt](https://raw.githubusercontent.com/hermitdave/FrequencyWords/525f9b560de45753a5ea01069454e72e9aa541c6/content/2018/en/en_full.txt) | `7fea67ab954e2c01df6c608c9826e594cf36f8823b3243554f88245fb75dc506` |
| [ru_full.txt](https://raw.githubusercontent.com/hermitdave/FrequencyWords/525f9b560de45753a5ea01069454e72e9aa541c6/content/2018/ru/ru_full.txt) | `32dfd94138aea2667ec455dcc8512029e39bd4b8ea70b09410d569b69d511a30` |
| [en_US-large.txt](https://raw.githubusercontent.com/en-wl/wordlist-diff/7f2f4078354752045ee16a041605686561122185/en_US-large.txt) | `815abb6f1855304a6c3f358aa1e5ce85823140a547226ce4114386f9b796c30f` |

## Next-word models

The next-word models in `en-next.json.gz` and `ru-next.json.gz` use English and Russian sentences from Tatoeba's separate **CC0** export. The 21 November 2026 snapshot contains 41,515 English rows and 23,109 Russian rows. The training script reads the published sentence text and drops the sentence ID and edit timestamp. [Tatoeba downloads and file descriptions](https://tatoeba.org/en/downloads) · [English CC0 archive](https://downloads.tatoeba.org/exports/per_language/eng/eng_sentences_CC0.tsv.bz2) · [Russian CC0 archive](https://downloads.tatoeba.org/exports/per_language/rus/rus_sentences_CC0.tsv.bz2) · [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/)

The aggregate count snapshots contain only transitions from Tatoeba’s CC0 export. The training directory also contains short conversation and technical examples written for Decktil, including software installation, version control and troubleshooting. These examples, the build script and Decktil’s contributions to the combined next-word tables are distributed under the [project’s MIT license](../../LICENSE).

The generator counts a transition once per distinct source sentence. The primary tables use transitions seen in at least two sentences and give the project examples a weight of 18. When a context has no primary continuation, the `*-backoff.tsv.gz` snapshots supply attested alternatives. The context must appear in two distinct sentences. For a three-word sequence, each adjacent pair must recur independently. For a two-word sequence, the following word needs at least eight observations as a successor in the corpus. Rankings use the supporting pair count or total successor count.

For an unfamiliar word, the optional `suffix` table records continuations shared by words with the same last two to four letters. At least six different predecessor words must support a two-letter ending, or four words for a longer ending. A continuation must follow at least three such words in the primary corpus counts, with one vote per spelling. Each context and ending retains at most five continuations. The bundled count snapshots allow the exact models to be rebuilt offline after Tatoeba rotates its downloads. Run from the repository root:

```sh
python3 tools/build_predictions.py
```

To regenerate the count snapshots, place the two original archives in one directory and run `python3 tools/build_predictions.py --from-exports /path/to/archives`. The generator checks their SHA-256 hashes.

| Archive | SHA-256 |
| --- | --- |
| `eng_sentences_CC0.tsv.bz2` | `c2e2edd063a31cbbc8e416a4858225e934bbb0afff485031821e59f230ccf1ca` |
| `rus_sentences_CC0.tsv.bz2` | `fb9e878c3d673c7ec5d7ec64d3ec0ae80a7df89f846f41e150144ba4bee4971f` |

## Russian morphology

`ru-morph.json.gz` contains inflections from the OpenCorpora morphological dictionary, revision 417150. Credit belongs to the OpenCorpora contributors. The input comes from the `pymorphy3-dicts-ru` distribution, version `2.4.417150.4580142`, whose dictionary data is licensed under Creative Commons Attribution–ShareAlike 3.0 Unported. Decktil selects and ranks families using its Russian completion list, derived from FrequencyWords. The resulting table is distributed under Creative Commons Attribution–ShareAlike 4.0 International. [OpenCorpora dictionary](https://opencorpora.org/dict.php) · [Packaged source and data license](https://pypi.org/project/pymorphy3-dicts-ru/2.4.417150.4580142/) · [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)

The generator looks up exact dictionary spellings for nouns, full adjectives, pronouns and numerals. It omits proper names, abbreviations and marked archaic, distorted or erroneous readings. Each entry records a spelling, possible cases, gender and number. Homonymous families and distinct gender or number readings remain separate. All included adjective families and the first 2,500 families by frequency retain their full eligible paradigms. Other families contribute spellings present in Decktil’s completion list.

Decktil also supplies explicit singular and plural forms of modern inanimate masculine technical nouns in `training/ru-tech-nouns.tsv`. Each row lists nominative singular and plural, then genitive, dative, instrumental and prepositional forms, first for singular, then for plural. Accusative matches nominative for these nouns. The builder adds these spellings to the completion list and uses the hand-reviewed paradigm for a noun that lacks an OpenCorpora noun entry in this model. Examples include `плагином`, `плагинов` and `деплоем`. The original project table is available under the [MIT license](../../LICENSE).

The table distinguishes the second locative and the partitive from the standard prepositional and genitive. For example, the entry for `лесу` allows the dative, second locative and partitive. The entry for `лесе` allows the standard prepositional.

The optional `companions` table ranks up to eight noun lemmas for each adjective. Training uses adjacent words from the same Tatoeba Russian CC0 export documented above and Decktil’s Russian chat examples. The generator checks case, number and singular gender, filters ambiguous parts of speech using OpenCorpora’s dictionary and tag probabilities, and rejects pairs with competing lemma readings. Rankings use the number of supporting observations, then the noun family’s frequency rank. [Tatoeba Russian CC0 export](https://downloads.tatoeba.org/exports/per_language/rus/rus_sentences_CC0.tsv.bz2) · [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/)

The bundled `training/ru-rare-companions.tsv.gz` stores aggregate lemma-pair counts for inflected word pairs that fall below the recurring-count threshold in `ru-counts.tsv.gz`. The derived counts are distributed with the morphology table under CC BY-SA 4.0. To regenerate them from the pinned original, pass `--from-export /path/to/rus_sentences_CC0.tsv.bz2`.

Run `python3 tools/build_morphology.py` to reproduce the table. The builder verifies and unpacks three pinned wheels in a temporary directory. It requires only Python’s standard library on the host. To rebuild offline, place the wheels for `pymorphy3==2.0.6`, `pymorphy3-dicts-ru==2.4.417150.4580142` and `dawg2-python==0.9.0` together and pass `--wheel-dir /path/to/wheels`. Decktil reads the bundled table offline. The builder is available under the [project’s MIT license](../../LICENSE).

## English Speller Database copyright notice

The following upstream notices accompany the English spelling filter.

```text
Copyright 2000-2026 by Kevin Atkinson

Permission to use, copy, modify, distribute, and sell any part of the English
Speller Database (ESDB, previously known as SCOWLv2), or word lists
created from it, is hereby granted without fee, provided that the above
copyright notice appears in all copies and that both the above copyright
notice and this notice appear in supporting documentation.  Kevin Atkinson
makes no representations about the suitability of this database for any
purpose.  It is provided "as is" without express or implied warranty.

ESDB is derived from many sources, most of which are in the Public Domain.
Data from the Corpus of Contemporary American English (COCA) was also used.

All data from COCA comes from 3-gram data that is not freely available;
however, the usage is within the rights given by the NDA that was signed when
purchasing the data.  More information on COCA is available at
https://www.english-corpora.org/coca/.

The primary source of words for ESDB comes from 12dicts and ENABLE2K.  Both
are in the Public Domain, but Alan Beale <biljir@pobox.com> deserves special
credit as he is the author of 12dicts and a major contributor to ENABLE2K.  In
addition, he gave me an incredible amount of feedback and created a number of
special lists in order to help improve the overall quality of ESDB.

===

If you are using an official speller dictionary created by ESDB that is not
Australian English, then no additional copyright applies and including the
notice before the === is sufficient.

If you are using the Australian English dictionary or a wordlist that uses the 'D'
SPELLING code and/or the 'AU' REGION code, the copyright after '=== AU' applies.

If you are using a generated word list larger than 80, the copyright
after '=== UKACD' applies.

If you are distributing the database or results from the
database that is not a speller dictionary or generated word list, then both
copyrights may apply depending on the parts you use.  In addition, the WordNet
copyright MIGHT apply as WordNet was one of many sources used for the initial
POS assignment.

=== AU

The initial information for Australian English comes from Benjamin Titze:

  Copyright 2016 by Benjamin Titze

  Permission to use, copy, modify, distribute and sell this array, the
  associated software, and its documentation for any purpose is hereby
  granted without fee, provided that the above copyright notice appears
  in all copies and that both that copyright notice and this permission
  notice appear in supporting documentation. Benjamin Titze makes no
  representations about the suitability of this array for any
  purpose. It is provided "as is" without express or implied warranty.

=== UKACD

Some words used come from the UK Advanced Cryptics Dictionary:

  Copyright (c) J Ross Beresford 1993-1999. All Rights Reserved.

  The following restriction is placed on the use of this publication:
  if The UK Advanced Cryptics Dictionary is used in a software package
  or redistributed in any form, the copyright notice must be
  prominently displayed and the text of this document must be included
  verbatim.

  There are no other restrictions: I would like to see the list
  distributed as widely as possible.

=== WordNet

WordNet was used to help with the initial POS assignment:

  This software and database is being provided to you, the LICENSEE,
  by Princeton University under the following license.  By obtaining,
  using and/or copying this software and database, you agree that you
  have read, understood, and will comply with these terms and
  conditions.:

  Permission to use, copy, modify and distribute this software and
  database and its documentation for any purpose and without fee or
  royalty is hereby granted, provided that you agree to comply with
  the following copyright notice and statements, including the
  disclaimer, and that the same appear on ALL copies of the software,
  database and documentation, including modifications that you make
  for internal use or for distribution.

  WordNet 1.6 Copyright 1997 by Princeton University.  All rights
  reserved.

  THIS SOFTWARE AND DATABASE IS PROVIDED "AS IS" AND PRINCETON
  UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR
  IMPLIED.  BY WAY OF EXAMPLE, BUT NOT LIMITATION, PRINCETON
  UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES OF MERCHANT-
  ABILITY OR FITNESS FOR ANY PARTICULAR PURPOSE OR THAT THE USE OF THE
  LICENSED SOFTWARE, DATABASE OR DOCUMENTATION WILL NOT INFRINGE ANY
  THIRD PARTY PATENTS, COPYRIGHTS, TRADEMARKS OR OTHER RIGHTS.

  The name of Princeton University or Princeton may not be used in
  advertising or publicity pertaining to distribution of the software
  and/or database.  Title to copyright in this software, database and
  any associated documentation shall at all times remain with
  Princeton University and LICENSEE agrees to preserve same.
```
