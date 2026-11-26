"""Type into the guarded Steam window through KWin's seat and its current XKB layout."""
from contextlib import contextmanager
import ctypes as C
import fcntl
import os
from pathlib import Path
import re
import subprocess
import time

import dbus
from evdev import UInput, UInputError, ecodes as E

from output import UnmappedKey


# Match physical XKB names to the keys Decktil can send. Xwayland normally
# assigns each evdev code an offset of 8.
_NAMES = {E.KEY_GRAVE: "TLDE", E.KEY_BACKSLASH: "BKSL", E.KEY_SPACE: "SPCE",
          E.KEY_102ND: "LSGT", E.KEY_LEFTSHIFT: "LFSH", E.KEY_RIGHTALT: "RALT"}
for row, start, length in (("AE", E.KEY_1, 12), ("AD", E.KEY_Q, 12),
                           ("AC", E.KEY_A, 11), ("AB", E.KEY_Z, 10)):
    _NAMES.update({start + i: f"{row}{i + 1:02d}" for i in range(length)})
_ACTIONS = {"backspace": (E.KEY_BACKSPACE, "BKSP", 0xff08),
            "enter": (E.KEY_ENTER, "RTRN", 0xff0d), "delete": (E.KEY_DELETE, "DELE", 0xffff),
            "left": (E.KEY_LEFT, "LEFT", 0xff51), "right": (E.KEY_RIGHT, "RGHT", 0xff53),
            "up": (E.KEY_UP, "UP", 0xff52), "down": (E.KEY_DOWN, "DOWN", 0xff54)}
_TEXT_CODES = tuple(k for k in _NAMES if k not in (E.KEY_LEFTSHIFT, E.KEY_RIGHTALT))
_NAMES.update({code: name for code, name, _ in _ACTIONS.values()})


def _bind(lib, name, result, *parameters):
    function = getattr(lib, name)
    function.restype = result
    function.argtypes = parameters
    return function


class _State(C.Structure):
    _fields_ = [("group", C.c_ubyte), ("locked_group", C.c_ubyte),
                ("base_group", C.c_ushort), ("latched_group", C.c_ushort),
                *[(name, C.c_ubyte) for name in ("mods", "base_mods", "latched_mods",
                  "locked_mods", "compat_state", "grab_mods", "compat_grab_mods",
                  "lookup_mods", "compat_lookup_mods")], ("ptr_buttons", C.c_ushort)]


_x = C.CDLL("libX11.so.6")
_p = C.c_void_p
_open = _bind(_x, "XOpenDisplay", _p, C.c_char_p)
_close = _bind(_x, "XCloseDisplay", C.c_int, _p)
_get_state = _bind(_x, "XkbGetState", C.c_int, _p, C.c_uint, C.POINTER(_State))
_focus = _bind(_x, "XGetInputFocus", C.c_int, _p, C.POINTER(C.c_ulong), C.POINTER(C.c_int))
_root = _bind(_x, "XDefaultRootWindow", C.c_ulong, _p)
_connection = _bind(C.CDLL("libX11-xcb.so.1"), "XGetXCBConnection", _p, _p)
_xcb = C.CDLL("libxcb.so.1")
_tree = _bind(_xcb, "xcb_query_tree", C.c_uint32, _p, C.c_uint32)
_tree_reply = _bind(_xcb, "xcb_query_tree_reply", _p, _p, C.c_uint32, _p)
_free = _bind(C.CDLL(None), "free", None, _p)
_lookup = _bind(_x, "XkbLookupKeySym", C.c_int, _p, C.c_ubyte, C.c_uint,
                C.POINTER(C.c_uint), C.POINTER(C.c_ulong))
_modmask = _bind(_x, "XkbKeysymToModifiers", C.c_uint, _p, C.c_ulong)
_unicode = _bind(C.CDLL("libxkbcommon.so.0"), "xkb_keysym_to_utf32", C.c_uint32, C.c_uint32)


@contextmanager
def _display(name):
    display = _open(os.fsencode(name))
    if not display:
        raise OSError("Cannot open the Xwayland display")
    try:
        yield display
    finally:
        _close(display)


def _parent(connection, window):
    # A focused child can disappear during a redraw. XCB returns a null reply
    # for BadWindow without running Xlib's default process-wide error handler.
    reply = _tree_reply(connection, _tree(connection, window), None)
    if not reply:
        return 0
    try:
        return C.c_uint32.from_address(reply + 12).value  # xcb_query_tree_reply_t.parent
    finally:
        _free(reply)


def _focused(display, xid):
    actual, revert = C.c_ulong(), C.c_int()
    if not _focus(display, C.byref(actual), C.byref(revert)):
        return False
    # Steam can give the search field's CEF child the raw X11 input focus.
    # Walk only from that child toward its root, never across to another client.
    window, root = actual.value, _root(display)
    connection = _connection(display)
    for _ in range(32):
        if window in (0, 1, root):
            return False
        if window == xid:
            return True
        window = _parent(connection, window)
    return False


def _current(display):
    state = _State()
    if _get_state(display, 0x0100, C.byref(state)):  # XkbUseCoreKbd
        raise OSError("Cannot read the effective keyboard state")
    locks = (2 & _modmask(display, 0xffe5)) | (16 & _modmask(display, 0xff7f))  # Caps/Num Lock
    if (state.base_mods or state.latched_mods or state.latched_group
            or state.mods & ~locks or state.group > 3):
        raise UnmappedKey("Release keyboard modifiers before typing")
    return (state.group << 13) | state.mods


def _symbol(display, code, state):
    consumed, symbol = C.c_uint(), C.c_ulong()
    if not _lookup(display, code + 8, state, C.byref(consumed), C.byref(symbol)):
        return 0, 0
    return symbol.value, consumed.value


def _strokes(display, requested, base):
    strokes = {}
    for name in requested.intersection(_ACTIONS):
        code, _, keysym = _ACTIONS[name]
        if _symbol(display, code, base)[0] == keysym:
            strokes[name] = (code,)
    modifiers = [(0, ())]
    if _symbol(display, E.KEY_LEFTSHIFT, base)[0] == 0xffe1 and _modmask(display, 0xffe1) == 1:
        modifiers.append((1, (E.KEY_LEFTSHIFT,)))
    altgr = _modmask(display, 0xfe03)
    if altgr in (32, 128) and _symbol(display, E.KEY_RIGHTALT, base)[0] == 0xfe03:
        modifiers.append((altgr, (E.KEY_RIGHTALT,)))
        if (len(modifiers) == 3 and _symbol(display, E.KEY_RIGHTALT, base | 1)[0] == 0xfe03
                and _symbol(display, E.KEY_LEFTSHIFT, base | altgr)[0] == 0xffe1):
            modifiers.append((altgr | 1, (E.KEY_LEFTSHIFT, E.KEY_RIGHTALT)))
    for mask, keys in modifiers:
        if requested == strokes.keys():
            return strokes
        for code in _TEXT_CODES:
            sym, consumed = _symbol(display, code, base | mask)
            scalar = _unicode(sym)
            if scalar and not mask & ~consumed:
                char = chr(scalar)
                if char in requested:
                    strokes.setdefault(char, (*keys, code))
    if requested != strokes.keys():
        raise UnmappedKey("Key unavailable in the current keyboard layout")
    return strokes


def _verify_codes(display):
    result = subprocess.run(["xkbcomp", "-xkb", display, "-"], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=2, check=False)
    names = {name: int(code) for name, code in re.findall(rb"<(\w+)>\s*=\s*(\d+);", result.stdout)}
    if result.returncode or any(names.get(name.encode()) != code + 8 for code, name in _NAMES.items()):
        raise OSError("Xwayland does not have the expected evdev keycodes")


class _WriteOnlyKeyboard(UInput):
    def _find_device(self, fd):
        # py-evdev normally opens /dev/input/eventN for reading and retries
        # for two seconds. SteamOS grants write access to uinput separately.
        return None


def _wait_for_kwin(device):
    manager = dbus.SessionBus().get_object("org.kde.KWin", "/org/kde/KWin/InputDevice", introspect=False)
    buffer = bytearray(64)
    # Linux uinput.h UI_GET_SYSNAME(64), _IOR('U', 44, char[64]).
    fcntl.ioctl(device.fd, 0x8040552c, buffer)
    sysname = buffer.split(b"\0", 1)[0].decode("ascii")
    if not re.fullmatch(r"input[0-9]+", sysname):
        raise OSError("uinput returned an invalid device name")
    events = {p.name for p in (Path("/sys/class/input") / sysname).glob("event[0-9]*")}
    deadline = time.monotonic() + .8
    while events.isdisjoint(manager.ListKeyboards(dbus_interface="org.kde.KWin.InputDeviceManager", timeout=.2)):
        if time.monotonic() >= deadline:
            raise OSError("KWin did not register the virtual keyboard")
        time.sleep(.025)


class EvdevOutput:
    def __init__(self, xid):
        if type(xid) is not int or not 1 < xid <= 0xffffffff:
            raise ValueError("A specific Xwayland window is required")
        self.xid = xid
        self.display = os.environ.get("DISPLAY")
        if not self.display:
            raise OSError("No Xwayland display")
        _verify_codes(self.display)
        # udev's keyboard classifier requires the complete first row of key bits.
        # Esc, Tab and Ctrl are advertised for detection and never sent.
        capabilities = sorted(set(_NAMES) | {E.KEY_ESC, E.KEY_TAB, E.KEY_LEFTCTRL})
        try:
            self.device = _WriteOnlyKeyboard({E.EV_KEY: capabilities}, name="Decktil Big Picture keyboard",
                                             phys="decktil-bigpicture/input0")
        except UInputError as error:
            raise OSError("Cannot open the virtual keyboard device") from error
        try:
            _wait_for_kwin(self.device)
        except dbus.DBusException as error:
            self.close()
            raise OSError("Cannot confirm virtual keyboard registration") from error
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.device is not None:
            self.device.close()
            self.device = None

    def _write(self, keys, guard):
        if self.device is None or not guard():
            return False
        with _display(self.display) as display:
            base = _current(display)
            strokes = _strokes(display, set(keys), base)
            for key in keys:
                if not guard() or not _focused(display, self.xid):
                    return False
                if _current(display) != base:
                    raise UnmappedKey("Keyboard layout changed during input")
                self._stroke(strokes[key])
        return True

    def _stroke(self, keys):
        held = []
        try:
            for code in keys:
                self.device.write(E.EV_KEY, code, 1)
                held.append(code)
            self.device.syn()
        finally:
            try:
                for code in reversed(held):
                    self.device.write(E.EV_KEY, code, 0)
                self.device.syn()
            except OSError:
                self.close()  # Unregister the device if its key releases could not be delivered.
                raise

    def text(self, value, guard):
        if not isinstance(value, str) or not 0 < len(value) <= 256 or not value.isprintable():
            raise ValueError("Expected printable keyboard text")
        return self._write(value, guard)

    def action(self, name, guard):
        if name not in _ACTIONS:
            raise UnmappedKey("Unsupported keyboard action")
        return self._write((name,), guard)
