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
    """Toggle Plasma once per physical desktop X or Steam+X press."""

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
        self.panel_seen = False
        self.deadline = 0
        self.next_activate = 0.0
        self.echo_until = 0.0
        self.recent_log_open = None
        self.close_after = 0.0
        self.binding_path = None
        self.binding_stamp = None
        self.binding_buttons = set()
        self.next_reconcile = 0
        self.log_identity = None
        self.log_offset = 0
        self.log_fragment = b""
        self.stock_open = False
        self.last_desktop_osk = None
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
        self.echo_until = 0.0
        self.recent_log_open = None
        self.stock_open = False
        self.last_desktop_osk = None
        self.binding_path = None

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
        """Remove a lingering Steam keyboard without dismissing the editor's panel."""
        now = time.monotonic()
        if not self.enabled or now < self.next_reconcile:
            return
        self.next_reconcile = now + 0.4
        if (self.stock_open or visible) and _steam_keyboard() is not None:
            self._close_stock(now)

    def _close_stock(self, now):
        if now >= self.close_after:
            self.close_after = now + 0.25
            _steam_request("close")

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
                if b"Path for App ID 413080," in line:
                    self.binding_path = None
                match = OSK_EVENT.search(line)
                if not match:
                    continue
                active, app = match.groups()
                desktop = (int(app) & 0xffffffff) in (mapping.DESKTOP, mapping.KEYBOARD)
                current = active == b"1" and desktop
                # The compositor can dismiss the popup before we read the log.
                # A close later in the same batch must not erase the request.
                opening |= current and not self.stock_open
                self.stock_open = current
            if opening and allowed and not mapping.running_game():
                self._desktop_opened()
        except (OSError, ValueError) as error:
            log.debug("Steam keyboard event unavailable: %s", error)
        return GLib.SOURCE_CONTINUE

    def _desktop_opened(self):
        now = time.monotonic()
        state = self._state()
        if not (state.get("available") and state.get("mode")):
            return
        self.last_desktop_osk = now
        self._close_stock(now)
        # Steam's action is a second observation of either physical shortcut.
        # Only a physical press toggles. A log-only request opens the panel.
        if self.armed or now <= self.echo_until:
            self._request(self.want_open)
        else:
            self.recent_log_open = (now, self.want_open if self.stage else bool(state.get("visible")))
            self._request(True)

    def _hide_panel(self):
        self.properties.Set(KEYBOARD_INTERFACE, "active", dbus.Boolean(False, variant_level=1), timeout=0.4)

    def _desktop_button_enabled(self, button):
        # Intercept only known, unambiguous bindings in the desktop layout.
        try:
            if self.binding_path is None or not self.binding_path.is_file():
                _, self.binding_path = mapping.desktop_source()
            stat = self.binding_path.stat()
            stamp = (stat.st_ino, stat.st_mtime_ns)
            if stamp != self.binding_stamp:
                layout = self.binding_path.read_text()
                self.binding_buttons = {name for name, finder in (("x", mapping.keyboard_x_binding),
                                                                  ("b", mapping.keyboard_b_binding))
                                        if finder(layout)}
                self.binding_stamp = stamp
            return button in self.binding_buttons
        except (OSError, ValueError, RuntimeError):
            return False

    def _desktop_x_enabled(self):
        return self._desktop_button_enabled("x")

    def desktop_b_closes_keyboard(self):
        return self._desktop_button_enabled("b")

    def update(self, guide_down, x_down):
        if not self.enabled:
            return
        previous, self.x = self.x, bool(x_down)
        now = time.monotonic()
        if self.armed and not x_down:
            self.armed = False
            self.echo_until = now + 0.85
        if previous is not False or not x_down:
            return
        try:
            if mapping.running_game():
                return
        except OSError:
            return
        state = self._state()
        if not (state.get("available") and state.get("mode")):
            return
        if not guide_down and not ((state.get("visible") or state.get("activeClientSupportsTextInput"))
                                   and self._desktop_x_enabled()):
            return
        was_visible = self.want_open if self.stage else bool(state.get("visible"))
        # A Steam log notification can beat the HID callback for the very
        # same press. Toggle against the visibility before that notification.
        if self.recent_log_open is not None and now - self.recent_log_open[0] < 0.25:
            was_visible = self.recent_log_open[1]
        self.recent_log_open = None
        self.armed = True
        self.echo_until = now + 0.85
        self._request(not was_visible)

    def _request(self, show):
        if self.stage and self.want_open == show:
            return
        self.want_open = show
        self.stage = "activate" if show else "dismiss"
        # KWin can accept the first activation while Qt is still loading
        # language data. Keep the temporary visibility policy until it shows.
        self.deadline = time.monotonic() + (5.0 if show and not self.panel_seen else 1.8)
        self.next_activate = 0.0
        if not self.timer:
            self.timer = GLib.timeout_add(25, self._advance)

    def _finish(self):
        self._restore_mode()
        self.stage = None
        self.timer = 0
        return GLib.SOURCE_REMOVE

    def _advance(self):
        now = time.monotonic()
        state = self._state()
        if now >= self.deadline:
            log.warning("Plasma keyboard did not reach requested visibility: %s", self.want_open)
            return self._finish()
        if not (state.get("available") and state.get("mode")):
            return self._finish()
        if bool(state.get("visible")) == self.want_open:
            if self.want_open:
                self.panel_seen = True
            return self._finish()
        if now < self.next_activate:
            return GLib.SOURCE_CONTINUE
        self.next_activate = now + 0.22
        try:
            if self.want_open:
                self._allow_mouse_activation(state)
                self.panel.forceActivate(timeout=0.4)
            else:
                self._hide_panel()
        except (dbus.DBusException, OSError, subprocess.SubprocessError) as error:
            log.debug("Plasma keyboard action pending: %s", error)
        return GLib.SOURCE_CONTINUE
