"""Read just the AppID of our helper executable from Steam's binary shortcut list."""
import struct
from pathlib import Path


def records(blob):
    pos = 0

    def string():
        nonlocal pos
        end = blob.index(0, pos)
        result = blob[pos:end].decode("utf-8", errors="replace")
        pos = end + 1
        return result

    def obj(depth):
        nonlocal pos
        if depth > 12:
            raise ValueError("Unexpected nesting in shortcuts.vdf")
        fields = {}
        while pos < len(blob):
            kind = blob[pos]
            pos += 1
            if kind == 8:
                return fields
            key = string().lower()
            if kind == 0:
                value = obj(depth+1)
            elif kind == 1:
                value = string()
            elif kind in (2, 3, 4, 6, 7):
                size = 8 if kind == 7 else 4
                if pos + size > len(blob):
                    raise ValueError("Truncated shortcut field")
                value = struct.unpack_from("<I",blob,pos)[0] if kind == 2 else None
                pos += size
            else:
                raise ValueError(f"Unsupported shortcut field type {kind}")
            fields[key] = value
        raise ValueError("Truncated shortcuts.vdf")
    tree = obj(0)
    shortcuts = tree.get("shortcuts", {})
    if not isinstance(shortcuts, dict):
        raise ValueError("Invalid shortcut list")
    return shortcuts.values()


def helper_records(steam_root, exe):
    for path in (steam_root / "userdata").glob("*/config/shortcuts.vdf"):
        try:
            shortcuts = records(path.read_bytes())
        except (OSError, ValueError, struct.error):
            continue
        for record in shortcuts:
            if not isinstance(record, dict):
                continue
            target = Path(str(record.get("exe", "")).strip('"'))
            try:
                appid = int(record.get("appid", 0)) & 0xffffffff
            except (ValueError, TypeError):
                continue
            if target == exe and appid >= 0x80000000:
                yield path.parent.parent.name, str(record.get("appname","")), appid


def lookup(steam_root, exe):
    ids = {entry[2] for entry in helper_records(steam_root, exe)}
    if len(ids) > 1:
        raise ValueError("Multiple Steam helpers found; remove duplicates from the library")
    return next(iter(ids), None)
