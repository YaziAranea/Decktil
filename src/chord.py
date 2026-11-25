"""Redirect Steam+X to the Plasma input panel in a Plasma Wayland session."""

import atexit
import logging
import os
from pathlib import Path
import re
import subprocess
import time

import dbus
from gi.repository import GLib
import profile as mapping


KEYBOARD_INTERFACE = "org.kde.kwin.VirtualKeyboard"
STEAM_KEYBOARD_TITLES = {
    "Steam Input On-screen Keyboard", "Экранная клавиатура системы ввода Steam",
}
log = logging.getLogger(__name__)
OSK_EVENT = re.compile(rb"\] Set OSK active ([01]) and appid (-?\d+)\r?$")


def _xdotool(*args):
    try:
        result = subprocess.run(
            ["/usr/bin/xdotool", *args], capture_output=True, text=True,
            timeout=0.5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _steam_keyboard():
    # Steam localizes the actual XWayland window title even in Desktop Mode.
    # xdotool's regex search drops UTF-8 titles on this XWayland; direct
    # getwindowname returns the full Russian title.
    ids = _xdotool("search", "--onlyvisible", "--class", "^steam$")
    for wid in (ids or "").splitlines():
        if not wid.isdecimal():
            continue
        if _xdotool("getwindowname", wid) not in STEAM_KEYBOARD_TITLES:
            continue
        pid = _xdotool("getwindowpid", wid)
        if not pid or not pid.isdecimal():
            continue
        try:
            binary = Path(f"/proc/{pid}/exe").readlink().name
        except OSError:
            continue
        if binary in {"steam", "steamwebhelper"}:
            return wid
    return None


def _steam_request(action):
    try:
        subprocess.Popen(
            ["/usr/bin/steam", "-if-running", f"steam://{action}/keyboard"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        log.warning("Could not ask Steam to %s its keyboard: %s", action, error)
        return False
    return True


class SteamChord:
    """Call update(guide_down, x_down) for HID reports and reset() on disconnect."""

    def __init__(self, session_bus):
        self.enabled = (
            os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            and "kde" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":")
            and not os.environ.get("GAMESCOPE_WAYLAND_DISPLAY")
            and bool(os.environ.get("DISPLAY"))
            and os.path.isfile("/usr/bin/xdotool")
        )
        proxy = session_bus.get_object("org.kde.KWin", "/VirtualKeyboard", introspect=False)
        self.properties = dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
        self.panel = dbus.Interface(proxy, KEYBOARD_INTERFACE)
        self.stage = None
        self.timer = 0
        self.x = None
        self.armed = False
        self.want_open = False
        self.deadline = 0
        self.previous_window = None
        self.restore_retried = False
        self.next_reconcile = 0
        self.log_identity = None
        self.log_offset = 0
        self.log_fragment = b""
        self.stock_open = False
        self.ignore_restore_until = 0
        self.overlap_since = None
        state_dir = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        self.mode_record = state_dir / "deck-plasma" / "chord-mode"
        if self.enabled:
            self._restore_mode()
            self.poll_desktop_actions(False)
            atexit.register(self._restore_mode)

    def reset(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        self._restore_mode()
        self.stage = None
        self.x = None
        self.armed = False

    def _state(self):
        try:
            return self.properties.GetAll(KEYBOARD_INTERFACE, timeout=0.4)
        except dbus.DBusException:
            return {}

    def _allow_mouse_activation(self, state):
        if int(state.get("mode", 0)) != 1:
            return
        if state.get("activeClientSupportsTextInput") and self.panel.willShowOnActive(timeout=0.4):
            return
        # KWin writes the mode into kwinrc even for D-Bus requests. Record
        # whether it was an implicit default before changing it, so a helper
        # restart during the short activation window can restore it too.
        previous = subprocess.run(
            ["/usr/bin/kreadconfig6", "--file", "kwinrc", "--group", "Wayland",
             "--key", "VirtualKeyboardMode", "--default", "implicit"],
            capture_output=True, text=True, check=True, timeout=1,
        ).stdout.strip()
        self.mode_record.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        draft = self.mode_record.with_suffix(".new")
        draft.write_text("implicit" if previous == "implicit" else "explicit")
        draft.replace(self.mode_record)
        self.properties.Set(KEYBOARD_INTERFACE, "mode", dbus.Int32(2, variant_level=1), timeout=0.4)

    def _restore_mode(self):
        try:
            previous = self.mode_record.read_text()
        except FileNotFoundError:
            return
        try:
            state = self._state()
            if not state or "mode" not in state:
                return
            mode = int(state["mode"])
            if mode == 2:
                self.properties.Set(KEYBOARD_INTERFACE, "mode", dbus.Int32(1, variant_level=1), timeout=0.4)
            if mode in (1, 2) and previous == "implicit":
                subprocess.run(
                    ["/usr/bin/kwriteconfig6", "--file", "kwinrc", "--group", "Wayland",
                     "--key", "VirtualKeyboardMode", "--delete"],
                    check=True, capture_output=True, timeout=1,
                )
            self.mode_record.unlink(missing_ok=True)
        except (dbus.DBusException, OSError, subprocess.SubprocessError) as error:
            log.warning("Could not restore the keyboard visibility policy: %s", error)

    def reconcile(self, visible):
        """Call periodically with the current KWin panel visibility."""
        if not (visible and self.enabled) or self.armed or self.stage:
            return
        now = time.monotonic()
        if now < self.next_reconcile:
            return
        self.next_reconcile = now + 0.4
        if _steam_keyboard() is not None:
            # A new Steam window can become viewable just before the matching
            # Desktop input event is appended. Give the event reader priority.
            if self.overlap_since is None:
                self.overlap_since = now
            elif now - self.overlap_since > 0.6:
                self._hide_panel()
        else:
            self.overlap_since = None

    def poll_desktop_actions(self, allowed=True):
        """Consume only new Steam input log events. Run from the main loop."""
        if not self.enabled:
            return GLib.SOURCE_CONTINUE
        path = mapping.ROOT / "logs/controller_ui.txt"
        try:
            pid = mapping.steam_pid()
            stat = path.stat()
            identity = (pid, stat.st_dev, stat.st_ino)
            if not pid or identity != self.log_identity or stat.st_size < self.log_offset:
                # Startup, Steam restart, rotation and truncation establish a
                # new baseline. Never replay an older keyboard opening.
                self.log_identity = identity
                self.log_offset = stat.st_size
                self.log_fragment = b""
                self.stock_open = False
                return GLib.SOURCE_CONTINUE
            if self.log_offset == stat.st_size:
                return GLib.SOURCE_CONTINUE
            with path.open("rb") as stream:
                stream.seek(self.log_offset)
                lines = (self.log_fragment + stream.read(256 * 1024)).split(b"\n")
                self.log_offset = stream.tell()
            self.log_fragment = lines.pop()
            opening = False
            for line in lines:
                match = OSK_EVENT.search(line)
                if not match:
                    continue
                active, app = match.groups()
                desktop = (int(app) & 0xffffffff) in (mapping.DESKTOP, mapping.KEYBOARD)
                current = active == b"1" and desktop
                opening = current and (opening or not self.stock_open)
                self.stock_open = current
            if opening and allowed and not mapping.running_game():
                self._desktop_opened()
        except (OSError, ValueError) as error:
            log.debug("Steam keyboard event unavailable: %s", error)
        return GLib.SOURCE_CONTINUE

    def _desktop_opened(self):
        now = time.monotonic()
        if self.stage == "verify_steam" or now < self.ignore_restore_until:
            self.ignore_restore_until = 0
            return
        if self.stage not in (None, "wait_steam"):
            return
        state = self._state()
        if not (state.get("available") and state.get("mode")):
            return
        if not self.armed and self.stage is None:
            self.want_open = not bool(state.get("visible"))
        self.armed = False
        if not self.want_open:
            self._hide_panel()
        # The authoritative Steam controller event makes this branch independent
        # of the physical HID report, title language and editor input protocol.
        self.previous_window = None
        _steam_request("close")
        self._set_stage("wait_closed", 0.8)
        if not self.timer:
            self.timer = GLib.timeout_add(45, self._advance)

    def _hide_panel(self):
        try:
            self.properties.Set(KEYBOARD_INTERFACE, "active", dbus.Boolean(False, variant_level=1), timeout=0.4)
        except dbus.DBusException as error:
            log.warning("Could not hide Plasma Keyboard: %s", error)

    def update(self, guide_down, x_down):
        if not self.enabled:
            return
        if self.x is False and x_down and guide_down and self.stage is None:
            try:
                desktop = not mapping.running_game()
            except OSError:
                desktop = False
            if not desktop:
                self.x = True
                return
            state = self._state()
            if state.get("available") and state.get("mode"):
                if _steam_keyboard() is None:
                    self.want_open = not bool(state.get("visible"))
                    self.armed = True
        if self.armed and (not x_down or not guide_down):
            self.armed = False
            if not self.want_open:
                self._hide_panel()
            self._set_stage("wait_steam", 1.6)
            self.timer = GLib.timeout_add(45, self._advance)
        self.x = bool(x_down)

    def _set_stage(self, stage, duration):
        self.stage = stage
        self.deadline = time.monotonic() + duration

    def _finish(self):
        self._restore_mode()
        self.stage = None
        self.timer = 0
        return GLib.SOURCE_REMOVE

    def _fallback(self):
        self._restore_mode()
        if _steam_keyboard() is not None:
            return self._finish()
        self.restore_retried = False
        self.ignore_restore_until = time.monotonic() + 3
        _steam_request("open")
        self._set_stage("verify_steam", 2)
        return GLib.SOURCE_CONTINUE

    def _advance(self):
        now = time.monotonic()
        if self.stage == "wait_steam":
            wid = _steam_keyboard()
            if wid is not None:
                state = self._state()
                if not (state.get("available") and state.get("mode")):
                    return self._finish()
                self.previous_window = wid
                _steam_request("close")
                self._set_stage("wait_closed", 0.65)
            elif now >= self.deadline:
                if not self.want_open:
                    return self._finish()
                self._set_stage("activate", 0.6)
        elif self.stage == "wait_closed":
            wid = _steam_keyboard()
            if wid is None and not self.stock_open:
                self._set_stage("activate", 0.6)
            elif now >= self.deadline:
                if wid is None or self.previous_window not in (None, wid):
                    return self._finish()
                # EWMH close is a fallback for Steam builds that ignore the URI.
                if _xdotool("windowquit", wid) is None:
                    return self._finish()
                self.previous_window = ""
                self.deadline = now + 0.6
        elif self.stage == "activate":
            state = self._state()
            try:
                if not self.want_open:
                    self._hide_panel()
                    return self._finish()
                # The Steam popup can briefly take focus. Give the original
                # editor time to regain its Wayland text-input context.
                if state.get("visible"):
                    return self._finish()
                if state.get("available") and (
                    state.get("activeClientSupportsTextInput") or now >= self.deadline
                ) and state.get("mode"):
                    self._allow_mouse_activation(state)
                    self.panel.forceActivate(timeout=0.4)
                    self._set_stage("verify", 0.8)
                elif now >= self.deadline:
                    log.info("Editor did not regain text input; restoring the Steam keyboard")
                    return self._fallback()
            except (dbus.DBusException, OSError, subprocess.SubprocessError) as error:
                log.warning("Could not toggle Plasma Keyboard: %s", error)
                if self.want_open:
                    return self._fallback()
                return self._finish()
        elif self.stage == "verify":
            if self._state().get("visible"):
                return self._finish()
            if now >= self.deadline:
                log.warning("Plasma Keyboard did not appear; restoring the Steam keyboard")
                return self._fallback()
        elif self.stage == "verify_steam":
            if self.stock_open or _steam_keyboard() is not None:
                return self._finish()
            if now >= self.deadline:
                if self.restore_retried:
                    log.error("No Steam keyboard appeared after two open requests")
                    return self._finish()
                self.restore_retried = True
                self.ignore_restore_until = now + 3
                _steam_request("open")
                self.deadline = now + 2
        return GLib.SOURCE_CONTINUE
