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
        match = CONFIG_LOAD.search(line)
        if match and int(match[1]) == app_id and int(match[2]) == index:
            # Only the most recent selection is evidence of actual ownership.
            local = LOADED.search(line)
            return (ROOT / local[3]).resolve() if local else None
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


def controller_mappings(layout):
    """Keep token offsets so changes to the helper preserve unrelated bindings verbatim."""
    tokens = (match for match in re.finditer(r'"(?:\\.|[^"\\])*"|[{}]|//[^\n]*', layout)
              if not match[0].startswith("//"))

    def read_pairs():
        pairs = []
        for name in tokens:
            if name[0] == "}":
                break
            value = next(tokens, None)
            if value is None or not name[0].startswith('"'):
                raise ValueError("Invalid Steam Input layout")
            pairs.append((name, read_pairs() if value[0] == "{" else value))
        return pairs

    return first(read_pairs(), "controller_mappings")


def children(pairs, key):
    return [v for n, v in pairs if n[0][1:-1] == key] if isinstance(pairs, list) else []


def first(pairs, key):
    return next(iter(children(pairs, key)), None)


def text(value):
    return value[0][1:-1] if isinstance(value, re.Match) else None


def stock_keyboard_binding(root):
    """Find a sole unambiguous X keyboard action in the default desktop action set."""
    default = next((p for p in children(root, "preset") if text(first(p, "id")) == "0"), None)
    sources = first(default, "group_source_bindings") or []
    diamond = next((text(group) for group, source in sources if text(source) == "button_diamond active"), None)
    if diamond is None:
        return None
    group = next((g for g in children(root, "group") if text(first(g, "id")) == diamond), None)
    button = first(first(group, "inputs"), "button_x")
    activators = first(button, "activators") or []
    if not isinstance(activators, list) or [text(name) for name, _ in activators] != ["Full_Press"]:
        return None
    bindings = first(activators[0][1], "bindings")
    if not isinstance(bindings, list) or len(bindings) != 1:
        return None
    name, value = bindings[0]
    binding = text(value)
    if text(name) == "binding" and binding and binding.split(",", 1)[0].strip() == "controller_action SHOW_KEYBOARD":
        return name.start(), value.end()
    return None


def keyboard_x_binding(layout):
    return stock_keyboard_binding(controller_mappings(layout))


def face_b_bindings(root):
    # In the helper the physical B is handled from HID. Remove Steam outputs for
    # all action sets and activators, including release, double-press and turbo.
    face_groups = {text(group) for preset in children(root, "preset")
                   for group, source in (first(preset, "group_source_bindings") or [])
                   if (text(source) or "").partition(" ")[0] == "button_diamond"}

    def commands(pairs):
        for key, value in pairs:
            if isinstance(value, list):
                yield from commands(value)
            elif text(key) == "binding":
                yield key.start(), value.end()

    for group in children(root, "group"):
        if text(first(group, "id")) in face_groups:
            for button in children(first(group, "inputs"), "button_b"):
                if isinstance(button, list):
                    yield from commands(button)


def inherited_template(source):
    original = source.read_text()
    if '"controller_neptune"' not in original or '"group_source_bindings"' not in original:
        raise RuntimeError("Unexpected Steam Deck desktop configuration")
    root = controller_mappings(original)
    spans = list(face_b_bindings(root))
    binding = stock_keyboard_binding(root)
    if binding:
        spans.append(binding)
    for start, end in sorted(spans, reverse=True):
        original = original[:start] + original[end:]
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

STEAM_UI = 769
OSK_RECOVERY_SECONDS = 5
STEAM_OSK = re.compile(r"Set OSK active ([01]) and appid (-?\d+)$")
CONFIG_LOAD = re.compile(r"Loaded Config for .*?Path for App ID (\d+), Controller (\d+):")


def osk_displaced(index, event_at, pinned):
    """Prove Steam's OSK just left a spurious URL-forced 769 profile."""
    if event_at is None:
        return False
    age = time.monotonic() - event_at
    if not 0 <= age <= OSK_RECOVERY_SECONDS:
        return False
    # Steam timestamps have one-second resolution. Anchor the log to the
    # observed desktop opening, never a previous keyboard in this session.
    earliest = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - age - 1))
    fallback = "OnFocusWindowChanged URL Forcing to window type: k_EWindowTypeGame, 769"
    stock_focus = "OnFocusWindowChanged On Screen Keyboard Forcing to window type: KB ActionSet, AppID 769"
    forced = closed = keyboard_focus = loaded = False
    for line in recent_log("controller_ui.txt"):
        if line[1:20] < earliest:
            return False
        focus = "OnFocusWindowChanged" in line
        if focus and line.endswith(fallback) and not closed:
            forced = True
        elif focus and line.endswith(stock_focus) and closed:
            keyboard_focus = True
        elif focus:
            # A game, Steam library or another real focus transition wins.
            return False
        config = CONFIG_LOAD.search(line)
        if config and int(config[2]) == index:
            if int(config[1]) != STEAM_UI:
                return False
            loaded = True
        match = STEAM_OSK.search(line)
        if not match:
            continue
        active, app = int(match[1]), int(match[2]) & 0xffffffff
        if not closed:
            if not forced or active != 0 or app != STEAM_UI:
                return False
            closed = True
        else:
            if not (active and keyboard_focus and loaded):
                return False
            if app != DESKTOP and not (pinned and app == KEYBOARD
                                       and selected_path(index, KEYBOARD) == TEMPLATE.resolve()):
                return False
            # The underlying foreground window can change without another
            # controller activation, for example when the user opens Steam.
            try:
                foreground = subprocess.run(
                    ["/usr/bin/xdotool", "getactivewindow", "getwindowclassname"],
                    capture_output=True, text=True, timeout=.4)
            except (OSError, subprocess.SubprocessError):
                return False
            return foreground.stdout.strip().lower() not in {"steam", "steamwebhelper"}
    return False


def forced_profile():
    """Return Steam's persistent URL-selected app, independent of the Guide action set."""
    for line in recent_log("controller_ui.txt"):
        if "OnFocusWindowChanged" not in line:
            continue
        if "On Screen Keyboard Forcing" in line:
            # The native OSK can briefly cover a still-pinned helper.
            continue
        match = re.search(r"OnFocusWindowChanged URL Forcing .*?, (?:AppID )?(-?\d+)$", line)
        # An automatic focus decision supersedes an older explicit pin.
        return (int(match[1]) & 0xffffffff) if match else None
    return None


def orphaned_index():
    """Find our held-over helper mapping while the calling bridge has a hidden panel."""
    try:
        if KEYBOARD is None or not steam_pid() or running_game():
            return None
        index, _ = desktop_source()
        if (active_app(index) == KEYBOARD
                and selected_path(index, KEYBOARD) == TEMPLATE.resolve()
                and forced_profile() in (None, 0, KEYBOARD)):
            return index
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def restore_desktop(index):
    # Only callers that just proved the active mapping belongs to the hidden
    # keyboard (or a verified displaced OSK) may briefly select Desktop.
    try:
        force(DESKTOP)
        if not wait_for(index, DESKTOP):
            raise RuntimeError("Steam did not restore its desktop mapping")
    finally:
        force(0)
    if not wait_for(index, DESKTOP, 0.24) and orphaned_index() == index:
        raise RuntimeError("Steam returned to the hidden keyboard mapping")


def release():
    if not STATE.is_file():
        index = orphaned_index()
        if index is not None:
            restore_desktop(index)
        return
    state = json.loads(STATE.read_text())
    if steam_pid() == state["pid"]:
        index = state["index"]
        current = active_app(index)
        # A close may beat the bridge's re-entry callback. Its existing lease
        # lets us check for a recent OSK round trip without taking over another UI.
        window_start = time.monotonic() - (OSK_RECOVERY_SECONDS - 0.5)
        displaced = (current == STEAM_UI and not running_game() and
                     osk_displaced(index, window_start, True))
        if forced_profile() == KEYBOARD or displaced:
            # Guide or a game can mask our URL pin. Clearing our own pin here
            # lets Steam choose what receives the input when Guide is released.
            force(0)
            if not running_game() and not wait_for(index, DESKTOP, 0.18):
                displaced = (active_app(index) == STEAM_UI and
                             osk_displaced(index, window_start, True))
                if displaced or orphaned_index() == index:
                    restore_desktop(index)
        elif orphaned_index() == index:
            restore_desktop(index)
    STATE.unlink(missing_ok=True)


def enter(event_at=None):
    if KEYBOARD is None:
        raise RuntimeError("Run setup.py enable after selecting the template in Steam")
    pid = steam_pid()
    if pid is None or running_game():
        raise RuntimeError("Steam desktop configuration unavailable")
    state = json.loads(STATE.read_text()) if STATE.is_file() else None
    if state and valid(state):
        return
    index, source = desktop_source()
    pinned = bool(state and state["pid"] == pid and state["index"] == index)
    current = active_app(index)
    if current != DESKTOP and not (current == STEAM_UI and osk_displaced(index, event_at, pinned)):
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
    args = sys.argv[1:]
    cmd = args[0] if len(args) == 1 else ""
    with (RUNTIME / "deck-plasma-steam-profile.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if len(args) == 2 and args[0] == "enter-after-osk":
            enter(float(args[1]))
        elif cmd == "enter":
            enter()
        elif cmd == "leave":
            release()
        elif cmd == "status":
            return 0 if STATE.is_file() and valid(json.loads(STATE.read_text())) else 1
        else:
            raise RuntimeError("Expected enter, enter-after-osk TIMESTAMP, leave or status")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
