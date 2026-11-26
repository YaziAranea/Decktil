"""Read-only Gamescope and AT-SPI checks for Steam's main Library search editor."""
from dataclasses import dataclass
from functools import lru_cache
import mmap
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

import dbus
import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from profile import ROOT, running_game


@dataclass(frozen=True)
class Skin:
    search: str
    positioner: str
    keyboard: str
    buffer: str
    modal: str
    standalone: str
    search_result: str = ""
    search_grid: str = ""


ACC = "org.a11y.atspi.Accessible"
COLLECTION = "org.a11y.atspi.Collection"
DOCUMENT = "org.a11y.atspi.Document"
REGISTRY = "org.a11y.atspi.Registry"
DBUS = "org.freedesktop.DBus"
BUS_PATH = "/org/freedesktop/DBus"
ROOT_PATH = "/org/a11y/atspi/accessible/root"
NULL = "/org/a11y/atspi/null"
ROLE = Atspi.Role
STATE = Atspi.StateType


@dataclass(frozen=True)
class Editor:
    xid: int
    pid: int
    bus: str
    document: str
    entry: str
    search: bool = False


def _skin_from_bundle(bundle):
    # The wrapper branch excludes modal, standalone, GameAPIOSK and desktop
    # overlay. A semantic change requires review. CSS module rehashes do not.
    signatures = (
        b'oe=he.VirtualKeyboardManager,Z=(0,i.u6)(),te=(0,i.Nr)(),Q=X.mode==a._5.StandaloneKeyboard,ae=he.IsDesktopOverlayWindow()',
        b'className:(0,A.A)(Be,!te&&!ae?d.VirtualKeyboardContainer:d.ModalVirtualKeyboardContainer',
        b'const ce=(0,y.OU)(I.BV.GamepadUI.GameAPIOSK())',
        b'return te||Q||ce||ae?Oe:(0,e.jsxs)("div",{className:d.Positioner',
        b'className:d.VirtualKeyboardTextBuffer,children:',
        b'On=(0,E.A)(z().SearchBox,z().Visible)',
        b'className:(0,E.A)(On,ki&&z().WhiteBackground),type:"search"',
    )
    if not all(bundle.find(marker) >= 0 for marker in signatures):
        return None

    def classes(anchor, names):
        index = bundle.find(anchor)
        if index < 0 or bundle.find(anchor, index + 1) >= 0:
            return {}
        start = bundle.rfind(b'.exports={', max(0, index-30000), index)
        end = bundle.find(b'}})', index, index+30000)
        if start < 0 or end < 0:
            return {}
        module = bundle[start:end]
        if b'}})' in module:
            return {}
        result = {}
        for name in names:
            tokens = re.findall(rb'\b'+name.encode()+rb':"([A-Za-z0-9_-]{4,100})"', module)
            if len(tokens) != 1:
                return {}
            result[name] = tokens[0].decode('ascii')
        return result

    search = classes(b'SearchFieldBackground:"', ['SearchBox'])
    results = classes(b'ResultTemplateImage:"', ['ResultTemplateImage', 'ResultsGridWrapper'])
    keys = classes(b'VirtualKeyboardStandaloneContainer:"', ['Positioner', 'VirtualKeyboardContainer', 'VirtualKeyboardTextBuffer',
                    'ModalVirtualKeyboardContainer', 'VirtualKeyboardStandaloneContainer'])
    if search and keys:
        return Skin(search['SearchBox'], keys['Positioner'], keys['VirtualKeyboardContainer'], keys['VirtualKeyboardTextBuffer'],
                    keys['ModalVirtualKeyboardContainer'], keys['VirtualKeyboardStandaloneContainer'],
                    results.get('ResultTemplateImage', ''), results.get('ResultsGridWrapper', ''))
    return None


@lru_cache(maxsize=1)
def _skin_at(identity):
    with (ROOT / "steamui/sp.js").open("rb") as stream:
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as bundle:
            return _skin_from_bundle(bundle)


def _known_skin():
    try:
        info = (ROOT / "steamui/sp.js").stat()
        return _skin_at((info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size))
    except (OSError, ValueError):
        return None


def _route(url, verified_search):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "steamloopback.host":
        return False
    entrypoints = ("/", "/index.html", "/sp.html", "/steamui/index.html", "/routes", "/routes/")
    if parts.fragment and parts.path not in entrypoints:
        return False
    path = parts.fragment.split("?", 1)[0] if parts.fragment else parts.path
    if parts.query and (not verified_search or parts.query not in
                        ("IN_GAMEPADUI", "IN_GAMEPADUI=true", "IN_GAMEPADUI=1")):
        return False
    if "%" in path:
        return False
    if path.startswith("/routes/"):
        path = path[len("/routes"):]
    if path in ("/library", "/library/home", "/search") or re.fullmatch(r"/library/tab/[0-9]+", path):
        return True
    return not parts.fragment and parts.path in entrypoints and verified_search


def _command(*args):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                          timeout=.35, check=True, env={**os.environ, "LC_ALL": "C"}).stdout


def _props(where, names):
    raw = _command("xprop", *where, *names)
    return dict(re.findall(r"^([A-Z_0-9]+)\([^)]*\) = (.+)$", raw, re.M))


def _matches_display(raw, display):
    # Gamescope exports the display name as CARDINAL words, not XA_STRING.
    if re.fullmatch(r":[0-9]{1,2}(?:\.0)?", display) is None:
        return False
    first = int(raw.partition(",")[0], 0).to_bytes(4, sys.byteorder)
    return first.partition(b"\0")[0] == display.removesuffix(".0").encode()


def _trusted(pid):
    proc = Path(f"/proc/{pid}")
    if pid <= 0 or proc.stat().st_uid != os.getuid():
        return False
    exe = (proc / "exe").resolve(strict=True)
    return any(exe == (ROOT / runtime / name).resolve() for runtime in
               ("steamrt64", "ubuntu12_32", "ubuntu12_64") for name in ("steam", "steamwebhelper"))


def _window():
    display = os.environ.get("DISPLAY", "")
    if not display or not os.environ.get("GAMESCOPE_WAYLAND_DISPLAY") or running_game():
        return None
    props = _props(["-root"], ["GAMESCOPE_FOCUSED_WINDOW", "GAMESCOPE_FOCUSED_APP",
                               "GAMESCOPE_FOCUSED_APP_GFX", "GAMESCOPE_FOCUS_DISPLAY",
                               "GAMESCOPE_KEYBOARD_FOCUS_DISPLAY", "STEAM_GAMES_RUNNING"])
    if (int(props["GAMESCOPE_FOCUSED_APP"], 0) != 769 or int(props["GAMESCOPE_FOCUSED_APP_GFX"], 0) != 769
            or int(props.get("STEAM_GAMES_RUNNING", "0"), 0) != 0
            or not all(_matches_display(props[key], display) for key in
                       ("GAMESCOPE_FOCUS_DISPLAY", "GAMESCOPE_KEYBOARD_FOCUS_DISPLAY"))):
        return None
    xid = int(props["GAMESCOPE_FOCUSED_WINDOW"], 0)
    if not xid or xid != int(_command("xdotool", "getwindowfocus")):
        return None
    props = _props(["-id", str(xid)], ["WM_CLASS", "_NET_WM_PID", "STEAM_GAME", "STEAM_BIGPICTURE",
                                       "STEAM_OVERLAY", "GAMESCOPE_EXTERNAL_OVERLAY"])
    pid = int(props["_NET_WM_PID"], 0)
    steam_classes = ('"steam", "steam"', '"steamwebhelper", "steam"')
    if (not _trusted(pid) or props.get("WM_CLASS", "").lower() not in steam_classes
            or (int(props.get("STEAM_GAME", "0"), 0) != 769 and int(props.get("STEAM_BIGPICTURE", "0"), 0) != 1)
            or any(int(props.get(key, "0"), 0) for key in ("STEAM_OVERLAY", "GAMESCOPE_EXTERNAL_OVERLAY"))):
        return None
    # Link process-scoped accessibility focus to this exact top-level X window.
    windows = _command("xdotool", "search", "--all", "--onlyvisible", "--pid", str(pid),
                       "--class", "^steam$").splitlines()
    return (xid, pid) if [int(value) for value in windows] == [xid] else None


def _states(words):
    return sum(int(value) << (32 * index) for index, value in enumerate(words))


def _has(state, *flags):
    return all(state & (1 << int(flag)) for flag in flags)


class _Tree:
    """Query only structural metadata. Never request text, values or accessible names."""
    def __init__(self):
        address = os.environ.get("AT_SPI_BUS_ADDRESS")
        if not address:
            session = dbus.SessionBus()
            if not session.name_has_owner("org.a11y.Bus"):
                raise ValueError("Accessibility bus is unavailable")
            address = session.call_blocking("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus",
                                            "GetAddress", "", (), timeout=.2)
        self.bus = dbus.bus.BusConnection(address)

    def start(self):
        self.deadline = time.monotonic() + .3

    def call(self, ref, iface, method, sig="", args=()):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        return self.bus.call_blocking(*ref, iface, method, sig, args, timeout=min(.15, remaining))

    def matches(self, ref, *, focused=False, attrs=None, role=None, visible=False, limit=3):
        mask = 1 << int(STATE.FOCUSED) if focused else 0
        if visible:
            mask |= (1 << int(STATE.SHOWING)) | (1 << int(STATE.VISIBLE))
        roles = [0, 0, 0, 0]
        if role is not None:
            bit = int(role) % 32
            # AT-SPI transports the bitset as signed D-Bus int32 words.
            roles[int(role) // 32] = -(1 << 31) if bit == 31 else 1 << bit
        rule = ([mask, 0], 1, attrs or {}, 1, roles, 1, [], 1, False)
        return self.call(ref, COLLECTION, "GetMatches", "(aiia{ss}iaiiasib)uib",
                         (rule, 1, limit, True))

    def editor(self, xid, pid, known):
        self.reason = "steam-accessibility-owner"
        apps = self.call((REGISTRY, ROOT_PATH), ACC, "GetChildren")
        candidates = []
        for ref in apps:
            if not ref[0].startswith(":"):
                continue
            owner = self.call((DBUS, BUS_PATH), DBUS, "GetConnectionUnixProcessID", "s", [ref[0]])
            if owner == pid:
                candidates.append(ref)
        if len(candidates) != 1:
            return None
        app, = candidates
        self.reason = "focused-control"
        focused = self.matches(app, focused=True)
        if len(focused) != 1 or focused[0][0] != app[0]:
            return None
        entry, = focused
        self.reason = "editor-type-or-state"
        role = self.call(entry, ACC, "GetRole")
        state = _states(self.call(entry, ACC, "GetState"))
        if (role not in (ROLE.ENTRY, ROLE.TEXT) or not _has(state, STATE.FOCUSED, STATE.EDITABLE,
                                                            STATE.VISIBLE, STATE.SHOWING, STATE.SENSITIVE)
                or state & ((1 << int(STATE.READ_ONLY)) | (1 << int(STATE.DEFUNCT)))):
            return None
        attrs = self.call(entry, ACC, "GetAttributes")
        if attrs.get("text-input-type", "text") not in ("text", "search", "textarea"):
            return None
        search = bool(known and known.search in attrs.get("class", "").split()
                      and attrs.get("tag") == "input" and attrs.get("text-input-type") == "search")
        if not search:
            self.reason = "unrecognized-client" if not known else "not-library-search"
            return None
        self.reason = "editor-ancestry"
        ref, doc, frame = entry, None, False
        for _ in range(32):
            ref = self.call(ref, "org.freedesktop.DBus.Properties", "Get", "ss", [ACC, "Parent"])
            if ref == app:
                break
            if ref[0] != app[0] or ref[1] == NULL:
                return None
            role = self.call(ref, ACC, "GetRole")
            if role in (ROLE.DIALOG, ROLE.ALERT, ROLE.EMBEDDED, ROLE.INTERNAL_FRAME, ROLE.PASSWORD_TEXT):
                return None
            if role == ROLE.DOCUMENT_WEB:
                url = self.call(ref, DOCUMENT, "GetAttributeValue", "s", ["URI"])
                if doc or not _route(url, search):
                    self.reason = "document-route" if known else "unrecognized-client-or-route"
                    return None
                doc = str(ref[1])
            if role in (ROLE.FRAME, ROLE.WINDOW):
                # Chromium omits ACTIVE on X11 frames. Exactly one visible AT-SPI
                # top-level in this process and one visible X window must agree.
                if frame:
                    return None
                top = self.call(app, ACC, "GetChildren")
                visible = [node for node in top if node[0] == app[0] and
                           _has(_states(self.call(node, ACC, "GetState")), STATE.SHOWING, STATE.VISIBLE)]
                if visible != [ref]:
                    self.reason = "ambiguous-accessible-window"
                    return None
                frame = True
        else:
            return None
        if doc and frame:
            self.reason = "verified"
            return Editor(xid, pid, str(app[0]), doc, str(entry[1]), search)
        return None

    def clear_to_wait(self, target, skin):
        """Look for obstructions in a bounded, complete set of exposed visible nodes.

        This only permits observing a future Steam OSK edge. Chromium can prune
        generic DOM nodes. A negative search cannot authorize hiding a keyboard.
        """
        if not target.search:
            return False
        doc = (target.bus, target.document)
        if self.matches(doc, role=ROLE.DIALOG):
            return False
        visible = self.matches(doc, visible=True, limit=257)
        if not visible or len(visible) > 256:
            return False
        markers = {skin.positioner, skin.keyboard, skin.modal, skin.standalone, skin.buffer}
        saw_entry = False
        for ref in visible:
            if ref[0] != target.bus:
                return False
            # Collection's attribute matcher compares whole strings. Tokenize
            # here so an additional theme class cannot hide an obstruction.
            classes = self.call(ref, ACC, "GetAttributes").get("class", "").split()
            if markers.intersection(classes):
                return False
            if ref[1] == target.entry and skin.search in classes:
                saw_entry = True
        return saw_entry

    def inline(self, target, prev, skin):
        doc = (target.bus, target.document)
        # Form controls, including password dialogs reached through Steamworks,
        # can coexist with a remembered Chromium editor and virtual focus.
        if self.matches(doc, attrs={"class": skin.buffer}) or self.matches(doc, role=ROLE.DIALOG):
            return None, None
        matches = self.matches(doc, attrs={"class": skin.positioner})
        if len(matches) > 1:
            return None, None
        if not matches:
            if prev is None:
                return None, None
            # Some renderers hide nodes from Collection while a modal takes
            # focus. Require the previously seen node itself to vanish or hide.
            try:
                state = _states(self.call(prev, ACC, "GetState"))
            except dbus.DBusException as exc:
                if exc.get_dbus_name() != "org.freedesktop.DBus.Error.UnknownObject":
                    raise
                return False, None
            if not _has(state, STATE.SHOWING, STATE.VISIBLE) or _has(state, STATE.DEFUNCT):
                return False, None
            return None, prev
        wrapper, = matches
        if wrapper[0] != target.bus:
            return None, None
        if not _has(_states(self.call(wrapper, ACC, "GetState")), STATE.SHOWING, STATE.VISIBLE):
            return False, None
        children = list(self.call(wrapper, ACC, "GetChildren"))
        for _ in range(30):
            if not children:
                break
            ref = children.pop(0)
            if ref[0] != target.bus:
                return None, None
            if (skin.keyboard in self.call(ref, ACC, "GetAttributes").get("class", "").split()
                    and _has(_states(self.call(ref, ACC, "GetState")), STATE.SHOWING, STATE.VISIBLE)):
                return True, wrapper
            children.extend(self.call(ref, ACC, "GetChildren"))
        return None, None


ERRORS = (OSError, ValueError, KeyError, OverflowError, subprocess.SubprocessError, dbus.DBusException)


class AccessibleBlockers:
    """Veto exposed credential controls and in-page dialogs when Steam supplies metadata.

    Steam's X11 shell can expose just empty top-level frames. Manual typing
    therefore uses the foreground window as its target even without a DOM tree.
    """
    def __init__(self):
        self.tree = self.app = self.pid = None
        self.retry = 0

    def _connect(self, pid):
        self.tree = _Tree()
        self.tree.start()
        self.pid, self.app = pid, None
        apps = self.tree.call((REGISTRY, ROOT_PATH), ACC, "GetChildren")
        for ref in apps:
            if ref[0].startswith(":") and self.tree.call(
                    (DBUS, BUS_PATH), DBUS, "GetConnectionUnixProcessID", "s", [ref[0]]) == pid:
                self.app = ref
                return

    def check(self, pid):
        now = time.monotonic()
        if now < self.retry and self.pid == pid:
            return None
        try:
            if self.pid != pid or not self.app:
                self._connect(pid)
                if not self.app:
                    self.retry = now + 2
                    return None
            self.tree.start()
            for node in self.tree.matches(self.app, focused=True):
                if node[0] != self.app[0]:
                    continue
                for _ in range(32):
                    if node == self.app or node[0] != self.app[0] or node[1] == NULL:
                        break
                    role = self.tree.call(node, ACC, "GetRole")
                    if role == ROLE.PASSWORD_TEXT:
                        return "password-field"
                    if role in (ROLE.DIALOG, ROLE.ALERT):
                        return "steam-dialog"
                    if role in (ROLE.ENTRY, ROLE.TEXT):
                        attrs = self.tree.call(node, ACC, "GetAttributes")
                        if attrs.get("text-input-type") == "password":
                            return "password-field"
                    if role == ROLE.DOCUMENT_WEB:
                        if self.tree.matches(node, role=ROLE.DIALOG, visible=True):
                            return "steam-dialog"
                        skin = _known_skin()
                        if skin and any(self.tree.matches(node, attrs={"class": marker}, visible=True)
                                        for marker in (skin.positioner, skin.standalone, skin.modal)):
                            return "native-keyboard"
                        break
                    node = self.tree.call(node, "org.freedesktop.DBus.Properties", "Get",
                                          "ss", [ACC, "Parent"])
        except ERRORS:
            self.pid, self.app, self.retry = pid, None, now + 2
        return None


class LibraryFocus:
    def __init__(self):
        self.tree = None
        self.reason = "not-checked"
        self.known = None
        self.previous = None

    def editor(self):
        self.reason = "no-idle-steam-library-window"
        try:
            window = _window()
            if window:
                self.known = _known_skin()
                if self.tree is None:
                    self.reason = "accessibility-bus"
                    self.tree = _Tree()
                self.reason = "accessibility-metadata"
                self.tree.start()
                target = self.tree.editor(*window, self.known)
                self.reason = self.tree.reason
                return target
        except ERRORS:
            self.tree = None
        return None

    def clear_to_wait(self, target):
        if target is None or not target.search or self.editor() != target or not self.known:
            return False
        try:
            self.tree.start()
            return self.tree.clear_to_wait(target, self.known)
        except ERRORS:
            return False

    def inline(self, target):
        if target is None or self.editor() != target or not self.known:
            self.previous = None
            return None
        try:
            self.tree.start()
            previous = self.previous[1] if self.previous and self.previous[0] == target else None
            result, ref = self.tree.inline(target, previous, self.known)
            self.previous = (target, ref) if ref is not None else None
            return result
        except ERRORS:
            self.previous = None
            return None
