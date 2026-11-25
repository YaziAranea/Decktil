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


def generate_layouts():
    layout_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    scale = trackpad_scale()
    for language in SYSTEM.iterdir():
        dest = layout_root / language.name
        if language.name not in {"ru_RU", "en_US", "fallback"}:
            if not dest.exists():
                dest.symlink_to(language, target_is_directory=True)
            continue
        dest.mkdir(exist_ok=True)
        local_delegate = dest / "DeckSelector.qml"
        if not local_delegate.exists():
            local_delegate.symlink_to(DELEGATE)
        row = dest / "DeckWordRow.qml"
        # QML resolves relative JS imports against the component's real path.
        write_layout(row, DELEGATE.with_name(row.name).read_text())
        code = "ru" if language.name == "ru_RU" else "en"
        dictionary = DELEGATE.parent / "words" / f"{code}.txt.gz"
        index = dest / "DeckWords.js"
        if not index.exists() or index.stat().st_mtime_ns < dictionary.stat().st_mtime_ns:
            buckets = {}
            with gzip.open(dictionary, "rt", encoding="utf-8") as words:
                for line in words:
                    word = line.strip()
                    buckets.setdefault(word[:2], []).append(word)
            with tempfile.NamedTemporaryFile("w", dir=dest, delete=False) as file:
                file.write(".pragma library\nvar words = ")
                json.dump(buckets, file, ensure_ascii=False, separators=(",", ":"))
            os.replace(file.name, index)
        for source in language.iterdir():
            target = dest / source.name
            if source.suffix != ".qml":
                if not target.exists():
                    target.symlink_to(source)
                continue
            body = source.read_text()
            # Each page gets its own delegate so symbol and locale changes drop old keys.
            body = re.sub(r"(?m)^([ \t]*)KeyboardLayout \{", lambda m:
                m[0] + "\n" + m[1] + "    id: deckLayout\n" + m[1] +
                f"    property QtObject deckSelector: DeckSelector {{ layout: deckLayout; typingScale: {scale} }}", body)
            if source.name == "main.qml":
                body = re.sub(r"(?m)^(\s*)KeyboardRow \{", r"\1DeckWordRow {}\n\1KeyboardRow {", body, count=1)
            write_layout(target, body)


try:
    generate_layouts()
except OSError as error:
    print(f"Controller layouts unavailable: {error}", file=sys.stderr)
else:
    os.environ["PLASMA_KEYBOARD_USE_QT_LAYOUTS"] = "1"
    os.environ["QT_VIRTUALKEYBOARD_LAYOUT_PATH"] = str(layout_root)
os.execv(KEYBOARD, [KEYBOARD])
