"""Manage Chrome Flatpak's per-application accessibility preferences."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

APP_ID = "com.google.Chrome"
ENV = {"ACCESSIBILITY_ENABLED": "1", "GTK_A11Y": "always"}
FLAG = "--force-renderer-accessibility"


def paths():
    home = Path.home()
    data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share")
    flatpak = Path(os.environ.get("FLATPAK_USER_DIR") or data / "flatpak")
    return (flatpak / "overrides" / APP_ID,
            home / ".var/app" / APP_ID / "config/chrome-flags.conf")


def installed():
    return bool(shutil.which("flatpak") and subprocess.run(
        ["flatpak", "info", APP_ID], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0)


def read(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError(f"Chrome configuration is not a regular file: {path}")
    return path.read_bytes().decode("utf-8", "surrogateescape") if path.exists() else ""


def write(path, value):
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as dest:
        dest.write(value.encode("utf-8", "surrogateescape"))
        os.fchmod(dest.fileno(), mode)
    os.replace(dest.name, path)


def checksum(value):
    return hashlib.sha256(value.encode("utf-8", "surrogateescape")).hexdigest()


def newline(value):
    return "\r\n" if "\r\n" in value else "\n"


def line_info(text, section, key):
    lines = text.splitlines(keepends=True)
    header = cell = None
    end = len(lines)
    active = False
    for i, line in enumerate(lines):
        label = line.strip()
        if re.fullmatch(r"\[[^]]+\]", label):
            if active:
                end = i
            active = label == f"[{section}]"
            if active:
                if header is not None:
                    raise RuntimeError(f"Duplicate Chrome override section [{section}]")
                header = i
        elif active and "=" in line and line.split("=", 1)[0].strip() == key:
            if cell is not None:
                raise RuntimeError(f"Duplicate Chrome override {key}")
            cell = i
    return lines, header, cell, end


def raw_line(text, section, key):
    lines, _, cell, _ = line_info(text, section, key)
    return lines[cell] if cell is not None else None


def value(line):
    return line.split("=", 1)[1].strip() if line is not None else ""


def set_line(text, section, key, line):
    lines, header, cell, end = line_info(text, section, key)
    if cell is not None:
        if line is not None and cell + 1 < len(lines) and not line.endswith("\n"):
            line += newline(text)
        lines[cell:cell + 1] = [line] if line is not None else []
    elif line is not None:
        nl = newline(text)
        if header is None:
            end = len(lines)
            lines.append(f"[{section}]" + nl)
            if end and not lines[end - 1].endswith("\n"):
                lines[end - 1] += nl
            end += 1
        if end and not lines[end - 1].endswith("\n"):
            lines[end - 1] += nl
        if end < len(lines) and not line.endswith("\n"):
            line += nl
        lines.insert(end, line)
    return "".join(lines)


def flag_state(text):
    args = [arg for line in text.splitlines() for arg in shlex.split(line, comments=True)]
    if any(arg == "--disable-renderer-accessibility" or
           (arg.startswith(FLAG + "=") and arg != FLAG + "=complete") for arg in args):
        return "conflict"
    return "on" if FLAG in args or FLAG + "=complete" in args else "off"


def status(ledger):
    managed = ledger.is_file()
    if not installed():
        return {"chrome_accessibility": "unavailable", "chrome_managed": managed}
    try:
        overrides, flags = map(read, paths())
        unset = value(raw_line(overrides, "Context", "unset-environment")).split(";")
        env = [value(raw_line(overrides, "Environment", key)) == wanted and key not in unset
               for key, wanted in ENV.items()]
        flag = flag_state(flags)
        state = "on" if all(env) and flag == "on" else "mixed" if any(env) or flag != "off" else "off"
    except (OSError, ValueError, RuntimeError):
        state = "mixed"
    return {"chrome_accessibility": state, "chrome_managed": managed}


def plan(original, flags, metadata):
    text = original
    rows = metadata["rows"] = []
    def change(section, key, wanted):
        nonlocal text
        old = raw_line(text, section, key)
        if old and value(old) == wanted:
            return
        prefix = old.split("=", 1)[0] if old else key
        end = re.search(r"(\r?\n)?\Z", old).group() if old else newline(text)
        new = prefix + "=" + wanted + end if wanted is not None else None
        rows.append({"section": section, "key": key, "old": old, "new": new})
        text = set_line(text, section, key, new)
    for key, wanted in ENV.items():
        change("Environment", key, wanted)
    unset = value(raw_line(text, "Context", "unset-environment")).split(";")
    if any(key in unset for key in ENV):
        remaining = [key for key in unset if key not in ENV]
        change("Context", "unset-environment", ";".join(remaining) if any(remaining) else None)
    kind = flag_state(flags)
    if kind == "conflict":
        raise RuntimeError("Chrome has a conflicting renderer accessibility flag in chrome-flags.conf")
    metadata["added_flag"] = kind == "off"
    if metadata["added_flag"]:
        nl = newline(flags)
        flags += (nl if flags and not flags.endswith("\n") else "") + FLAG + nl
    return text, flags


def restore(text, flags, metadata):
    # If the user edited a setting after enabling this feature, their edit wins.
    user_values = {key for key in ENV if value(raw_line(text, "Environment", key)) != ENV[key]}
    for row in reversed(metadata["rows"]):
        section, key = row["section"], row["key"]
        current = raw_line(text, section, key)
        if key == "unset-environment" and (current != row["new"] or user_values):
            # Reinsert only the masks we removed. Keep later additions or removals.
            tokens = [x for x in value(current).split(";") if x]
            previous = value(row["old"]).split(";")
            add = [x for x in previous if x in ENV and x not in user_values and x not in tokens]
            if add:
                text = set_line(text, section, key, key + "=" + ";".join(tokens + add) + ";" + newline(text))
        elif current == row["new"] or (current and row["new"]
                                       and current.rstrip("\r\n") == row["new"].rstrip("\r\n")):
            text = set_line(text, section, key, row["old"])
    if metadata["added_env_section"]:
        lines, header, _, end = line_info(text, "Environment", "")
        if header is not None and header + 1 == end:
            del lines[header]
            text = "".join(lines)
    if metadata["added_flag"]:
        lines = flags.splitlines(keepends=True)
        for index in reversed(range(len(lines))):
            if lines[index].rstrip("\r\n") == FLAG:
                del lines[index]
                break
        flags = "".join(lines)
    return text, flags


def configure(choice, ledger):
    files = paths()
    if choice == "off" and not ledger.is_file():
        return
    if choice == "on" and not installed():
        raise RuntimeError("Google Chrome Flatpak is not installed")
    before = [read(path) for path in files]
    if ledger.is_file():
        if choice == "on" and status(ledger)["chrome_accessibility"] == "on":
            return
        old = json.loads(read(ledger))
        restored = restore(*before, old)
        for path, current, result, info in zip(files, before, restored, old["files"]):
            # Restore the exact former line ending when our append completed an
            # unterminated final line. Unrelated later edits are kept as written.
            if info["no_eol"] and checksum(result.removesuffix(newline(result))) == info["hash"]:
                result = result.removesuffix(newline(result))
            if read(path) != current:
                raise RuntimeError("Chrome configuration changed during the update. Try again.")
            if not info["existed"] and result == "":
                path.unlink(missing_ok=True)
            elif result != current:
                write(path, result)
        ledger.unlink()
        if choice == "off":
            return
        before = [read(path) for path in files]
    info = {"added_env_section": line_info(before[0], "Environment", "")[1] is None,
            "files": [{"hash": checksum(text), "existed": path.exists(), "no_eol": bool(text and not text.endswith("\n"))}
                      for path, text in zip(files, before)]}
    after = plan(*before, info)
    if tuple(before) == after:
        return
    # Record only the Chrome settings we own before the first rename. Off and
    # retry can undo a partially applied change after a crash.
    write(ledger, json.dumps(info))
    try:
        for path, current, result in zip(files, before, after):
            if result != current:
                if read(path) != current:
                    raise RuntimeError("Chrome configuration changed during the update. Try again.")
                write(path, result)
    except (OSError, RuntimeError):
        configure("off", ledger)
        raise
