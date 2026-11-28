"""Store Decktil's keyboard palette in an INI file readable by QtCore.Settings."""

import configparser
from contextlib import suppress
import fcntl
import os
from pathlib import Path
import re
import tempfile
import time


COLORS = ("backgroundStart", "backgroundEnd", "keyStart", "keyEnd", "text", "accent")
DIRECTIONS = ("vertical", "horizontal")


def _palette(background_start, background_end, key_start, key_end, text, accent, direction):
    return dict(zip((*COLORS, "direction"), (background_start, background_end, key_start,
                                            key_end, text, accent, direction)))


# The default swatch represents Breeze Dark in previews; the keyboard follows the actual KDE theme.
PRESETS = {
    "default": _palette("#31363B", "#31363B", "#454B50", "#454B50", "#FCFCFC", "#3DAEE9", "vertical"),
    "porcelain": _palette("#EBE5DB", "#CDD3DC", "#FFFCF6", "#F2F0EF", "#252B36", "#2857BA", "vertical"),
    "midnight": _palette("#121B31", "#2D2847", "#39435B", "#2F304B", "#F5F4FF", "#AFA6FF", "vertical"),
    "lagoon": _palette("#112F38", "#164E51", "#28585D", "#23434D", "#F2FFFA", "#78E7C0", "horizontal"),
    "ember": _palette("#321D27", "#513025", "#66424A", "#50313A", "#FFF3E8", "#FFB270", "horizontal"),
}


def readable_ink(palette):
    """Choose black or white by its worst contrast across the key and panel gradients."""
    def channels(key):
        value = palette[key]
        return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def linear(value):
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    luminances = []
    for surface in ("key", "background"):
        start, end = channels(surface + "Start"), channels(surface + "End")
        # Each RGB channel interpolates before conversion to relative luminance.
        # Opposing channel changes can make the middle darker than either end.
        for step in range(65):
            position = step / 64
            rgb = (linear(a + (b - a) * position) for a, b in zip(start, end))
            red, green, blue = rgb
            luminances.append(0.2126 * red + 0.7152 * green + 0.0722 * blue)
    black_contrast = (min(luminances) + 0.05) / 0.05
    white_contrast = 1.05 / (max(luminances) + 0.05)
    return "#000000" if black_contrast >= white_contrast else "#FFFFFF"


def _path():
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "decktil" / "appearance.ini"


def _read(path):
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    try:
        with path.open(encoding="utf-8") as stream:
            config.read_file(stream)
    except (OSError, UnicodeError, configparser.Error):
        config.clear()
    return config


def _validated(config):
    if not hasattr(config, "get"):
        raise ValueError("Ожидалась палитра клавиатуры")
    preset = config.get("preset")
    if isinstance(preset, str) and preset in PRESETS:
        return {"preset": preset, **PRESETS[preset]}
    if preset != "custom":
        raise ValueError("Неизвестная тема клавиатуры")
    result = {"preset": preset}
    for field in COLORS:
        value = config.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise ValueError("Цвет должен быть записан в формате #RRGGBB")
        result[field] = value.upper()
    if config.get("direction") not in DIRECTIONS:
        raise ValueError("Неизвестное направление градиента")
    result["direction"] = config["direction"]
    return result


def load():
    config = _read(_path())
    with suppress(KeyError, ValueError):
        return _validated(config["Appearance"])
    return _validated({"preset": "default"})


def load_custom():
    config = _read(_path())
    with suppress(KeyError, ValueError):
        return _validated({**config["Custom"], "preset": "custom"})
    return None


def number_row_visible():
    try:
        return _read(_path()).getboolean("Layout", "numberRow", fallback=True)
    except ValueError:
        return True


def save(config, *, number_row=None):
    selected = _validated(config)
    path = _path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Managers in different install locations share this file. Preserve the most recent
    # custom palette when another window only changes the active preset.
    with (path.parent / ".appearance.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        settings = _read(path)
        if number_row is not None:
            if not settings.has_section("Layout"):
                settings.add_section("Layout")
            settings.set("Layout", "numberRow", "true" if number_row else "false")
        settings["Appearance"] = selected
        if selected["preset"] == "custom":
            settings["Custom"] = selected
        with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as file:
            temporary = Path(file.name)
            try:
                settings.write(file, space_around_delimiters=False)
                file.flush()
                if path.exists():
                    # QSettings compares timestamps at millisecond resolution.
                    stamp = max(time.time_ns(), path.stat().st_mtime_ns + 1_000_000)
                    os.utime(temporary, ns=(stamp, stamp))
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    return selected
