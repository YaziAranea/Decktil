"""Foreground guard for a manually invoked keyboard on KDE's Steam Xwayland window."""
import os
import re
import subprocess

import dbus

from focus import AccessibleBlockers, _command, _props, _trusted, running_game
from chord import STEAM_KEYBOARD_TITLES


STEAM_CLASSES = {'"steamwebhelper", "steam"', '"steam", "steam"'}
ROOT_ATOMS = ("_NET_ACTIVE_WINDOW", "_NET_CLIENT_LIST_STACKING")
WINDOW_ATOMS = ("WM_CLASS", "_NET_WM_PID", "_NET_WM_STATE", "_NET_WM_WINDOW_TYPE",
                "WM_TRANSIENT_FOR", "STEAM_GAME", "STEAM_OVERLAY", "GAMESCOPE_EXTERNAL_OVERLAY")
POPUP_TYPES = {"_NET_WM_WINDOW_TYPE_DIALOG", "_NET_WM_WINDOW_TYPE_DROPDOWN_MENU",
               "_NET_WM_WINDOW_TYPE_POPUP_MENU"}


def _kde_desktop():
    return (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            and "kde" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":")
            and not os.environ.get("GAMESCOPE_WAYLAND_DISPLAY")
            and re.fullmatch(r":[0-9]+(?:\.0)?", os.environ.get("DISPLAY", "")) is not None)


def _stack():
    flags = []
    for atom in ROOT_ATOMS:
        flags.extend(["-f", atom, "32x", r" = $0+\n"])
    data = _props(["-root", *flags], ROOT_ATOMS)
    owner = int(data[ROOT_ATOMS[0]], 16)
    windows = [int(word, 16) for word in data[ROOT_ATOMS[1]].split(", ")]
    if not 0 < owner <= 0xffffffff or len(windows) > 256 or owner not in windows:
        return None, []
    return owner, windows[windows.index(owner) + 1:]


def _window(xid):
    return _props(["-id", hex(xid), "-f", "WM_TRANSIENT_FOR", "32x", r" = $0\n"], WINDOW_ATOMS)


def _atoms(value):
    return set(value.split(", "))


def _is_main(props):
    state = _atoms(props.get("_NET_WM_STATE", ""))
    return (props.get("WM_CLASS", "").lower() in STEAM_CLASSES
            and {"_NET_WM_STATE_FULLSCREEN", "_NET_WM_STATE_FOCUSED"} <= state
            and not state.intersection({"_NET_WM_STATE_MODAL", "_NET_WM_STATE_HIDDEN"})
            and not _atoms(props.get("_NET_WM_WINDOW_TYPE", "")).intersection(POPUP_TYPES)
            and not int(props.get("WM_TRANSIENT_FOR", "0"), 0)
            and int(props.get("STEAM_GAME", "0"), 0) == 769
            and not int(props.get("STEAM_OVERLAY", "0"), 0)
            and not int(props.get("GAMESCOPE_EXTERNAL_OVERLAY", "0"), 0)
            and _trusted(int(props.get("_NET_WM_PID", "0"), 0)))


def _obstruction(owner, other):
    props = _window(other)
    state = _atoms(props.get("_NET_WM_STATE", ""))
    if "_NET_WM_STATE_HIDDEN" in state:
        return None
    steam = (props.get("WM_CLASS", "").lower() in STEAM_CLASSES
             and _trusted(int(props.get("_NET_WM_PID", "0"), 0)))
    modal = ("_NET_WM_STATE_MODAL" in state or int(props.get("WM_TRANSIENT_FOR", "0"), 0) == owner
             or bool(_atoms(props.get("_NET_WM_WINDOW_TYPE", "")).intersection(POPUP_TYPES)))
    if not steam and not modal:
        return None
    if "Map State: IsViewable" not in _command("xwininfo", "-id", hex(other), "-stats"):
        return None
    if steam and _command("xdotool", "getwindowname", str(other)).strip() in STEAM_KEYBOARD_TITLES:
        return "native-keyboard"
    return "competing-window"


class BigPictureFocus:
    """Only the active, unlocked Steam Big Picture XID may receive explicit key presses."""

    def __init__(self):
        self.panel_xid = None
        self.reason = "not-checked"
        self._bus = None
        self._accessible = AccessibleBlockers()

    def _locked(self):
        if self._bus is None:
            self._bus = dbus.SessionBus()
        return bool(self._bus.call_blocking("org.freedesktop.ScreenSaver", "/ScreenSaver",
                                            "org.freedesktop.ScreenSaver", "GetActive", "", (),
                                            timeout=.25))

    def target(self, *, allow_inline=False):
        if not _kde_desktop():
            self.reason = "not-kde-wayland"
            return None
        try:
            self.reason = "unverified-foreground"
            owner, above = _stack()
            if not owner:
                return None
            props = _window(owner)
            if not _is_main(props):
                return None
            self.reason = "game-running"
            if running_game():
                return None
            self.reason = "screen-lock"
            if self._locked():
                return None
            self.reason = "competing-window"
            if len(above) > 24:
                return None
            for window in above:
                if window != self.panel_xid:
                    blocker = _obstruction(owner, window)
                    if blocker:
                        self.reason = blocker
                        return None
            blocker = self._accessible.check(int(props["_NET_WM_PID"], 0))
            if blocker and not (allow_inline and blocker == "native-keyboard"):
                self.reason = blocker
                return None
            self.reason = "keyboard-focus"
            if int(_command("xdotool", "getwindowfocus").strip()) != owner:
                return None
            self.reason = "verified"
            return owner
        except (OSError, KeyError, ValueError, OverflowError, dbus.DBusException, subprocess.SubprocessError):
            return None

    editor = target

    def matches(self, xid):
        return type(xid) is int and self.target() == xid

    def native_visible(self):
        from chord import _steam_keyboard
        if not _kde_desktop():
            return False
        try:
            xid = _steam_keyboard()
            return bool(xid and _trusted(int(_window(int(xid)).get("_NET_WM_PID", "0"), 0)))
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            return False
