#!/usr/bin/python
"""Temporarily use the Deck keyboard controls on the Plasma desktop."""
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import time

STEAM = Path.home() / ".steam"
ROOT = STEAM / "steam"
TEMPLATE = ROOT / "controller_base/templates/deck_plasma_keyboard.vdf"
TEMPLATE_DIGEST = (Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
                   / "deck-plasma-keyboard/template.sha256")
DESKTOP = 413080
# Steam chooses a local AppID when importing the helper shortcut.
SHORTCUT = Path(__file__).with_name("steam-shortcut.json")
def shortcut_app_id():
    try:
        app_id = json.loads(SHORTCUT.read_text())["appid"]
    except FileNotFoundError:
        return None
    if type(app_id) is not int or not 0x80000000 <= app_id <= 0xffffffff:
        raise RuntimeError("Invalid Steam helper shortcut AppID")
    return app_id

KEYBOARD = shortcut_app_id()
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
STATE = RUNTIME / "deck-plasma-steam-profile.json"
ACTION = re.compile(r'("(?:(?:left|right)_(?:trackpad|trigger)) )active(")')
FOCUS = re.compile(r"Queueing activation for controller: (\d+) app: (-?\d+)")
LOADED = re.compile(r"Loaded Config for Local Selection Path for App ID (\d+), Controller (\d+): (.+)")

def steam_start():
    pid = steam_pid()
    if not pid:
        raise RuntimeError("Steam is not running")
    info = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    start_ticks = int(info[19])
    boot = time.time() - time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(
        boot + start_ticks / os.sysconf("SC_CLK_TCK") - 1))


def recent_log(name):
    # Steam rotates its controller logs independently. Read further only when
    # the caller has not found its event, and ignore previous Steam sessions.
    cutoff = steam_start()
    current = ROOT / "logs" / name
    for path in (current, current.with_suffix(".previous.txt")):
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            stream.seek(0, 2)
            pos = stream.tell()
            carry = b""
            while pos:
                size = min(pos, 128 * 1024)
                pos -= size
                stream.seek(pos)
                lines = (stream.read(size) + carry).split(b"\n")
                carry = lines.pop(0) if pos else b""
                for raw in reversed(lines):
                    if not raw.startswith(b"["):
                        continue
                    line = raw.decode(errors="replace").rstrip("\r")
                    if line[1:20] < cutoff:
                        return
                    yield line


def atomic_text(path, content):
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def steam_pid():
    try:
        pid = int((STEAM / "steam.pid").read_text().strip())
        if Path(f"/proc/{pid}/exe").resolve().name == "steam":
            return pid
    except (OSError, ValueError):
        pass
    return None

def running_game():
    registry = (STEAM / "registry.vdf").read_text(errors="replace")
    match = re.search(r'"RunningAppID"\s+"(\d+)"', registry, re.I)
    return match is None or int(match[1]) != 0

def force(app_id):
    subprocess.run(["/usr/bin/steam", "-ifrunning", f"steam://forceinputappid/{app_id}"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=True)

def active_app(index):
    for line in recent_log("controller.txt"):
        match = FOCUS.search(line)
        if match and int(match[1]) == index:
            return int(match[2]) & 0xffffffff
    return None

def selected_path(index, app_id):
    for line in recent_log("controller_ui.txt"):
        match = LOADED.search(line)
        if match and int(match[1]) == app_id and int(match[2]) == index:
            return (ROOT / match[3]).resolve()
    return None

DESKTOP_LOADED = re.compile(r"Loaded Config for [^:]*?Path for App ID (\d+), Controller (\d+): (.+)")
def desktop_source():
    for line in recent_log("controller_ui.txt"):
        match = DESKTOP_LOADED.search(line)
        if match and int(match[1]) == DESKTOP:
            path = (ROOT / match[3]).resolve()
            # A fresh Deck may use Valve's stock desktop layout. For custom
            # layouts derive only from the Neptune layout actually selected.
            if path == (ROOT / "controller_base/desktop_neptune.vdf").resolve() or (
                path.parent.name == str(DESKTOP) and path.name == "controller_neptune.vdf"
            ):
                return int(match[2]), path
            break
    raise RuntimeError("No current Steam Desktop Neptune layout found. Save a personal Desktop Layout in Steam first.")


def inherited_template(source):
    original = source.read_text()
    if '"controller_neptune"' not in original or '"group_source_bindings"' not in original:
        raise RuntimeError("Unexpected Steam Deck desktop configuration")
    result = ACTION.sub(r'\1inactive\2', original).replace(str(source), str(TEMPLATE.resolve()))
    return re.sub(r'(?m)^(\s*"title"\s*)"[^"\n]*"', r'\1"Deck Plasma Keyboard"', result, count=1)


def watch_refresh():
    # Steam normally opens the template on each activation. When the desktop
    # bindings changed, require a real read of the newly replaced inode.
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "inotify_init1")
    if libc.inotify_add_watch(fd, os.fsencode(TEMPLATE), 1) < 0:  # IN_ACCESS
        os.close(fd)
        raise OSError(ctypes.get_errno(), "inotify_add_watch")
    return fd


def template_was_read(fd):
    try:
        return any(mask & 1 for _, mask, _, _ in struct.iter_unpack(
            "iIII", os.read(fd, 4096)))
    except BlockingIOError:
        return False


def wait_for(index, app_id, timeout=1.6):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if active_app(index) == app_id:
            return True
        time.sleep(0.04)
    return False

def valid(state):
    return (steam_pid() == state["pid"] and not running_game()
            and active_app(state["index"]) == KEYBOARD
            and selected_path(state["index"], KEYBOARD) == TEMPLATE.resolve())

def release():
    if not STATE.is_file():
        return
    state = json.loads(STATE.read_text())
    if steam_pid() == state["pid"]:
        index = state["index"]
        was_ours = active_app(index) == KEYBOARD
        force(0)
        if was_ours and not running_game() and not wait_for(index, DESKTOP, 0.18):
            # Steam builds can retain the last mapping until a focus event.
            if active_app(index) == KEYBOARD:
                try:
                    force(DESKTOP)
                    if not wait_for(index, DESKTOP):
                        raise RuntimeError("Steam did not restore its desktop mapping")
                finally:
                    force(0)
    STATE.unlink(missing_ok=True)

def enter():
    if KEYBOARD is None:
        raise RuntimeError("Run setup.py enable after selecting the template in Steam")
    if STATE.is_file():
        state = json.loads(STATE.read_text())
        if valid(state):
            return
        release()
    pid = steam_pid()
    if pid is None or running_game():
        raise RuntimeError("Steam desktop configuration unavailable")
    index, source = desktop_source()
    if active_app(index) != DESKTOP:
        raise RuntimeError("Steam Deck is not using its desktop configuration")
    expected = inherited_template(source)
    refresh = not TEMPLATE.is_file() or TEMPLATE.read_text() != expected
    fd = None
    try:
        if refresh:
            atomic_text(TEMPLATE, expected)
            atomic_text(TEMPLATE_DIGEST, hashlib.sha256(expected.encode()).hexdigest())
            fd = watch_refresh()
        state = {"index": index, "pid": pid}
        atomic_text(STATE, json.dumps(state))
        force(KEYBOARD)
        if not wait_for(index, KEYBOARD) or not valid(state):
            raise RuntimeError("Steam did not activate the Deck keyboard configuration")
        if fd is not None and not template_was_read(fd):
            raise RuntimeError("Steam did not reload the updated Deck keyboard configuration")
    except BaseException:
        release()
        if refresh:
            # Require another verified reload before accepting a later attempt.
            TEMPLATE.unlink(missing_ok=True)
        raise
    finally:
        if fd is not None:
            os.close(fd)


def main():
    cmd = sys.argv[1] if len(sys.argv) == 2 else ""
    with (RUNTIME / "deck-plasma-steam-profile.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if cmd == "enter":
            enter()
        elif cmd == "leave":
            release()
        elif cmd == "status":
            return 0 if STATE.is_file() and valid(json.loads(STATE.read_text())) else 1
        else:
            raise RuntimeError("Expected enter, leave or status")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
