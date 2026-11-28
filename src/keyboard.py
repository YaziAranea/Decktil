#!/usr/bin/python3
"""Give Plasma's own RU/EN layouts a controller input delegate for this process."""
import os
import gzip
import json
from pathlib import Path
import re
import sys
import tempfile

SYSTEM = Path("/usr/share/plasma/keyboard/layouts")
DELEGATE = Path(__file__).resolve().with_name("DeckSelector.qml")
KEYBOARD = "/usr/bin/plasma-keyboard"
layout_root = Path(os.environ["XDG_RUNTIME_DIR"]) / "deck-plasma-keyboard" / "layouts"


def trackpad_scale():
    userdata = Path.home() / ".steam/steam/userdata"
    configs = sorted(userdata.glob("*/config/localconfig.vdf"),
                     key=lambda path: path.stat().st_mtime, reverse=True)
    for config in configs:
        with config.open(errors="replace") as stream:
            for line in stream:
                value = re.search(r'"keyboard_trackpad_typing_scale"\s+"([\d.]+)"', line)
                if value:
                    try:
                        return min(4.0, max(0.1, float(value[1])))
                    except ValueError:
                        continue
    return 1.0


def write_layout(path, body):
    if not path.is_symlink() and path.is_file() and path.read_text() == body:
        return
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as file:
        file.write(body)
    os.replace(file.name, path)


WORDS_HEADER = ".pragma library\n// Completion index with one-letter buckets\n"
NEXT_HEADER = ".pragma library\n// Next-word index with suffix contexts\n"
# Only the small first-letter buckets use these everyday conversation choices.
# The full two-letter buckets retain corpus order for longer prefixes and spelling.
FIRST_LETTER_PICKS = (
    "привет пока почему спасибо сейчас сегодня да давай для можно мне могу "
    "буду будет был хорошо хочу хочешь фото файл фильм йогурт йога йод "
    "ёлка ёжик ёмкость щас щётка щенок юг юмор юрист ыкать ыканье "
    "hello how have yes you your please people play do did don't just job join "
    "x-ray xbox xylophone zero zone zoom"
).split()


def current_index(path, stamp, header):
    if not path.is_file() or path.stat().st_mtime_ns != stamp:
        return False
    marker = header.encode()
    with path.open("rb") as stream:
        return stream.read(len(marker)) == marker


def write_predictions(index, model):
    # An exact timestamp match also detects replacement by an older model.
    try:
        stamp = model.stat().st_mtime_ns
        if current_index(index, stamp, NEXT_HEADER):
            return
        with gzip.open(model, "rt", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or data.get("format") != 1:
            raise ValueError("unsupported prediction model")
        source = []
        for key in ("bigram", "trigram", "suffix"):
            table = data.get(key, {} if key == "suffix" else None)
            if not isinstance(table, dict):
                raise ValueError(f"missing {key} predictions")
            source.append("var " + key + " = " + json.dumps(table, ensure_ascii=False, separators=(",", ":")))
    except (OSError, EOFError, ValueError) as error:
        # The keyboard and its ordinary prefix completions remain usable.
        print(f"Next-word suggestions unavailable for {model.name}: {error}", file=sys.stderr)
        write_layout(index, NEXT_HEADER + "var bigram = {}\nvar trigram = {}\nvar suffix = {}\n")
        return
    write_layout(index, NEXT_HEADER + "\n".join(source) + "\n")
    os.utime(index, ns=(stamp, stamp))


def write_completions(index, dictionary, spelling_index=None):
    stamp = dictionary.stat().st_mtime_ns
    spelling_current = spelling_index is None or (spelling_index.is_file()
                                                   and spelling_index.stat().st_mtime_ns == stamp)
    if spelling_current and current_index(index, stamp, WORDS_HEADER):
        return
    buckets = {}
    initial = {}
    spelling = {}
    with gzip.open(dictionary, "rt", encoding="utf-8") as words:
        for rank, line in enumerate(words, 1):
            word = line.strip()
            if not word:
                continue
            buckets.setdefault(word[:2], []).append(word)
            # A short frequency list supplies both literal suggestions and the
            # Russian inflection engine. Avoid subtitle fragments beginning with
            # a soft/hard sign or ы. Attested ы headwords are picked below.
            if len(word) > 1 and word[0] not in "ьъы":
                first = initial.setdefault(word[0], [])
                if len(first) < 64:
                    first.append(word)
            if spelling_index is not None:
                spelling.setdefault(word[:2], {})[word[2:]] = rank
    for word in reversed(FIRST_LETTER_PICKS):
        if word in buckets.get(word[:2], ()):
            first = initial.setdefault(word[0], [])
            if word in first:
                first.remove(word)
            first.insert(0, word)
            del first[64:]
    buckets.update(initial)
    write_layout(index, WORDS_HEADER + "var words = " +
                 json.dumps(buckets, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.utime(index, ns=(stamp, stamp))
    if spelling_index is not None:
        write_layout(spelling_index, ".pragma library\nvar spelling = " +
                     json.dumps(spelling, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.utime(spelling_index, ns=(stamp, stamp))


def write_morphology(index, dictionary):
    empty = ".pragma library\nvar model = null\n"
    if dictionary is None:
        write_layout(index, empty)
        return
    try:
        stamp = dictionary.stat().st_mtime_ns
        if index.is_file() and index.stat().st_mtime_ns == stamp:
            return
        with gzip.open(dictionary, "rt", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or data.get("format") != 1 or not isinstance(data.get("families"), list):
            raise ValueError("unsupported Russian morphology format")
        families = data["families"]
        by_word = {}
        for family_id, (lemma, _, forms) in enumerate(families):
            # One spelling can have several readings in the same family.
            surfaces = {form[0] for form in forms}
            surfaces.add(lemma)
            for word in sorted(surfaces):
                by_word.setdefault(word, []).append(family_id)
        companions = data.get("companions", {})
        if not isinstance(companions, dict):
            raise ValueError("invalid Russian adjective and noun combinations")
        payload = json.dumps({"families": families, "byWord": by_word, "companions": companions},
                             ensure_ascii=False, separators=(",", ":"))
    except (OSError, EOFError, TypeError, IndexError, ValueError) as error:
        print(f"Russian inflection suggestions unavailable: {error}", file=sys.stderr)
        write_layout(index, empty)
        return
    # A single JSON string avoids compiling hundreds of thousands of QML
    # JavaScript array and object literals when the Russian layout opens.
    write_layout(index, ".pragma library\nvar model = JSON.parse(" + json.dumps(payload, ensure_ascii=False) + ")\n")
    os.utime(index, ns=(stamp, stamp))


def add_alternate_keys(layout):
    """Extend plain letter and period keys in the local copy of a Plasma layout."""
    def extend(match):
        block = match[0]
        if not re.search(r'''(?m)^\s*text:\s*["'][a-zа-яё.]["']\s*$''', block, re.IGNORECASE):
            return block
        indent = match["indent"] + "    "
        inherited = re.search(r"(?m)^[ \t]*alternativeKeys:[ \t]*(.+)$", block)
        value = inherited[1] if inherited else "[]"
        block = re.sub(r"(?m)^[ \t]*(?:alternativeKeys|smallText|smallTextVisible):[^\n]*\n", "", block)
        additions = (
            "readonly property bool deckHasAlternates: true",
            f"alternativeKeys: Alternates.keys(text, {value})",
            "smallText: Alternates.hint(text, alternativeKeys, uppercased)",
            "smallTextVisible: false",
            "DeckKeyHint { hintColor: deckLayout.deckAppearance.textColor }",
        )
        start, rest = block.split("\n", 1)
        return start + "\n" + "".join(indent + line + "\n" for line in additions) + rest

    layout = re.sub(r"(?ms)^(?P<indent>[ \t]*)Key[ \t]*\{.*?^(?P=indent)\}", extend, layout)
    return layout.replace("import QtQuick\n", 'import QtQuick\nimport "DeckAlternates.js" as Alternates\n', 1)


def generate_layouts(*, gaming=False):
    layout_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    # All layout pages share one QML singleton, including when changing languages.
    style = layout_root / "DecktilStyle"
    style.mkdir(exist_ok=True)
    write_layout(style / "DeckPalette.qml", DELEGATE.with_name("DeckPalette.qml").read_text())
    write_layout(style / "qmldir", "singleton Palette 1.0 DeckPalette.qml\n")
    scale = trackpad_scale()
    for language in SYSTEM.iterdir():
        dest = layout_root / language.name
        if language.name not in {"ru_RU", "en_US", "fallback"}:
            if not dest.exists():
                dest.symlink_to(language, target_is_directory=True)
            continue
        dest.mkdir(exist_ok=True)
        local_delegate = dest / "DeckSelector.qml"
        if gaming:
            write_layout(local_delegate, DELEGATE.read_text())
        elif not local_delegate.exists():
            local_delegate.symlink_to(DELEGATE)
        # QML resolves relative JS imports against the component's real path.
        components = ("DeckAlternates.js", "DeckHold.qml", "DeckKeyHint.qml", "DeckAppearance.qml",
                      "DeckPrediction.js", "DeckGrammar.js", "DeckNumberRow.qml")
        if gaming:
            components += ("DeckGameWordRow.qml",)
        else:
            components += ("DeckWordRow.qml", "DeckPersonal.qml", "DeckLearning.js")
        for component in components:
            write_layout(dest / component, DELEGATE.with_name(component).read_text())
        # SteamOS resolves en_US/main.fallback in the fallback layout directory.
        if not gaming or (language / "main.qml").is_file():
            code = "ru" if language.name == "ru_RU" else "en"
            dictionary = DELEGATE.parent / "words" / f"{code}.txt.gz"
            write_predictions(dest / "DeckNext.js", dictionary.parent / f"{code}-next.json.gz")
            write_completions(dest / "DeckWords.js", dictionary, None if gaming else dest / "DeckSpelling.js")
            write_morphology(dest / "DeckMorphData.js", dictionary.parent / "ru-morph.json.gz" if code == "ru" else None)
        for source in language.iterdir():
            target = dest / source.name
            if source.suffix != ".qml":
                if not target.exists():
                    target.symlink_to(source)
                continue
            body = source.read_text()
            if gaming:
                # An external overlay cannot focus a Qt editor. Let the game
                # panel own Shift and bind the native key labels to that state.
                body = re.sub(r"(?m)^(\s*)Key \{", r"\1Key {\n\1    uppercased: decktilGamePanel.shifted && !noModifier", body)
                body = re.sub(r"(?m)^(\s*)ShiftKey \{", r"\1ShiftKey {\n\1    enabled: true", body)
            # Each page gets its own delegate so symbol and locale changes drop old keys.
            game_host = "; gamePanel: decktilGamePanel" if gaming else ""
            body = re.sub(r"(?m)^([ \t]*)KeyboardLayout \{", lambda m:
                m[0] + "\n" + m[1] + "    id: deckLayout\n" + m[1] +
                "    property QtObject deckAppearance: DeckAppearance { layout: deckLayout }\n" + m[1] +
                f"    property QtObject deckSelector: DeckSelector {{ layout: deckLayout; typingScale: {scale}{game_host} }}", body)
            if source.name == "main.qml":
                body = add_alternate_keys(body)
                row = "DeckGameWordRow { host: decktilGamePanel }" if gaming else "DeckWordRow {}"
                number_row = "DeckNumberRow { visible: deckLayout.deckAppearance.palette.showNumberRow }"
                body = re.sub(r"(?m)^([ \t]*)KeyboardRow \{", rf"\1{row}\n\1{number_row}\n\1KeyboardRow {{", body, count=1)
            write_layout(target, body)


if __name__ == "__main__":
    try:
        generate_layouts()
    except OSError as error:
        print(f"Controller layouts unavailable: {error}", file=sys.stderr)
    else:
        os.environ["PLASMA_KEYBOARD_USE_QT_LAYOUTS"] = "1"
        os.environ["QT_VIRTUALKEYBOARD_LAYOUT_PATH"] = str(layout_root)
    os.execv(KEYBOARD, [KEYBOARD])
