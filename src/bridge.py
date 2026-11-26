#!/usr/bin/python3
"""Route Deck pad positions to the open Plasma input method in Desktop Mode."""
from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import signal
import sqlite3
import struct
import subprocess
import sys
import time

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib, GLibUnix

from chord import SteamChord
from field_return import BrowserFieldReturn
from haptics import TrackpadFeedback
from popup import SteamPopupGuard
import personal
import profile as mapping

INTERFACE = "local.deck.PlasmaKeyboard"
VK = "org.kde.kwin.VirtualKeyboard"
log = logging.getLogger("deck-plasma-keyboard")
PRESS_SAMPLE_SECONDS = .008
OSK_INPUT_SECONDS = 1.2
ERASE_BUTTON = 16  # Frame bit reserved for physical X on an open panel.


@dataclass
class InputFrame:
    time: float
    touches: int
    pressed: int
    held: int
    preload: int
    force: list[int]


def deck_device():
    for entry in Path("/sys/class/hidraw").glob("hidraw*"):
        device = (entry / "device").resolve()
        if ("HID_ID=0003:000028DE:00001205" in (device / "uevent").read_text()
                and (device.parent / "bInterfaceNumber").read_text().strip() == "02"):
            return Path("/dev") / entry.name
    return None


def trigger_mask(report, previous):
    left, right = struct.unpack_from("<HH", report, 44)
    mask = 0
    for bit, value, digital in ((1, left, 2), (2, right, 1)):
        threshold = 4200 if previous & bit else 8200
        if value >= threshold or report[8] & digital:
            mask |= bit
    return mask


def preload_mask(forces, touches, held, previous):
    """One soft detent while pressing into each trackpad, rearmed on relaxation."""
    mask = 0
    for side, force in enumerate(forces):
        bit = 1 << side
        threshold = 350 if previous & bit else 950
        if touches & bit and (force >= threshold or held & bit):
            mask |= bit
    return mask


def display_force(raw):
    # Reserve more of the ring's travel for an ordinary thumb press while
    # retaining the full sensor range for a deliberate hard squeeze.
    value = min(32767, max(0, raw))
    return round(31 * (32767 + 3200) * value / (32767 * (value + 3200)))


class Bridge(dbus.service.Object):
    def __init__(self, bus):
        self.name = dbus.service.BusName(INTERFACE, bus, do_not_queue=True)
        super().__init__(self.name, "/Pads")
        self.chord = SteamChord(bus)
        self.field_return = BrowserFieldReturn(bus, self.reopen_browser_field)
        self.popup_guard = SteamPopupGuard(bus)
        self.props = self.chord.properties
        self.fd = None
        self.steam_pid = None
        self.watch = 0
        self.visible = False
        self.locked = False
        self.ready = False
        self.verified_pin = None
        self.route_until = 0.0
        self.panel_epoch = 0
        self.job = None
        self.retry = 0.0
        self.next_mouse_check = 0.0
        self.enter_error = ""
        self.last_frame = None
        self.last_send = 0.0
        self.last_down = None
        self.erase_serial = None
        self.erase_release_deadline = 0.0
        self.desktop_erase_until = 0.0
        self.held_triggers = 0
        self.preloaded = 0
        self.feedback = TrackpadFeedback()
        self.frame_serial = 0
        self.recent_frames = {}
        self.force_samples = [None, None]
        self.click_timers = set()
        bus.add_signal_receiver(self.visibility, signal_name="visibleChanged",
                                dbus_interface=VK, bus_name="org.kde.KWin", path="/VirtualKeyboard")
        for changed in ("activeClientSupportsTextInputChanged", "activeChanged"):
            bus.add_signal_receiver(self.editor_changed, signal_name=changed, dbus_interface=VK,
                                    bus_name="org.kde.KWin", path="/VirtualKeyboard")
        bus.add_signal_receiver(self.lock_changed, signal_name="ActiveChanged",
                                dbus_interface="org.freedesktop.ScreenSaver",
                                bus_name="org.freedesktop.ScreenSaver", path="/ScreenSaver")
        try:
            lock = bus.get_object("org.freedesktop.ScreenSaver", "/ScreenSaver", introspect=False)
            self.locked = bool(lock.GetActive(dbus_interface="org.freedesktop.ScreenSaver", timeout=.5))
        except dbus.DBusException:
            pass
        self.game_running = self.in_game = False
        self.maintenance()
        GLib.timeout_add(45, self.poll_desktop_actions)
        GLib.timeout_add(900, self.maintenance)

    @dbus.service.signal(INTERFACE, signature="iiiiiiiiii")
    def Frame(self, lx, ly, rx, ry, touches, pressed, held, left_force, right_force, serial):
        """Positions, contacts, edges (low bits click, next two preload), held, pressure, serial."""

    @dbus.service.signal(INTERFACE, signature="")
    def CancelRepeat(self):
        """Cancel an active erase gesture on Wayland editor deactivation."""

    @dbus.service.signal(INTERFACE, signature="")
    def PersonalChanged(self):
        """Invalidate personal suggestions and outstanding learning events."""

    @dbus.service.method(INTERFACE, in_signature="", out_signature="")
    def PersonalSettingsChanged(self):
        self.PersonalChanged()

    @dbus.service.method(INTERFACE, in_signature="", out_signature="s")
    def PersonalSession(self):
        return "" if self.locked or self.in_game else personal.session()

    @dbus.service.method(INTERFACE, in_signature="sssss", out_signature="")
    def LearnWord(self, generation, locale, prev2, prev1, word):
        if self.visible and not self.locked and not self.in_game:
            try:
                personal.learn(generation, locale, prev2, prev1, word)
            except (OSError, sqlite3.Error):
                pass  # Typing continues with the built-in dictionary on storage errors.

    @dbus.service.method(INTERFACE, in_signature="ssss", out_signature="as")
    def PersonalSuggestions(self, locale, prev2, prev1, prefix):
        if self.visible and not self.locked and not self.in_game:
            try:
                return personal.suggestions(locale, prev2, prev1, prefix)
            except (OSError, sqlite3.Error):
                pass
        return []

    @dbus.service.method(INTERFACE, in_signature="i", out_signature="")
    def CloseOnEmpty(self, serial):
        # The layout sees the editor's surrounding/selected/preedit text. The
        # bridge owns KWin activation and can authenticate the current X edge.
        now = time.monotonic()
        if (self.erase_serial != int(serial) or not self.visible or self.locked or self.in_game
                or now > self.erase_release_deadline or not self.recovery_editor()):
            return
        if not self.can_route() and not (now < self.desktop_erase_until and self.acquiring_from_desktop()):
            return
        self.erase_serial = None
        self.chord._request(False)

    @dbus.service.method(INTERFACE, in_signature="iii", out_signature="")
    def Feedback(self, serial, clicked, hovered):
        # Confirm only the edges that this layout actually resolved to enabled keys.
        # Drop duplicate or late replies, especially across a hide or layout switch.
        frame = self.recent_frames.pop(int(serial), None)
        if not frame or not self.can_route() or not self.visible or self.locked or self.in_game:
            return
        age = time.monotonic() - frame.time
        if age > .3:
            return
        click = clicked & frame.pressed
        hover = hovered & frame.touches & ~frame.held
        preload = (hovered >> 2) & frame.preload
        sampling = any(click & (1 << side) and sample is frame
                       for side, sample in enumerate(self.force_samples))
        if not sampling or age >= PRESS_SAMPLE_SECONDS:
            self.feedback.play(self.fd, click, hover, preload, frame.force)
            return
        # At the digital edge a gentle and a firm squeeze can both read the
        # same threshold. Allow two more 250 Hz samples before confirming
        # the accepted key. Each press owns its peak, even across rapid taps.
        self.feedback.play(self.fd, 0, hover, preload, frame.force)
        def confirm():
            self.click_timers.discard(timer)
            if self.can_route() and self.visible and not self.locked and not self.in_game:
                self.feedback.play(self.fd, click, 0, 0, frame.force)
            return GLib.SOURCE_REMOVE
        timer = GLib.timeout_add(max(1, math.ceil((PRESS_SAMPLE_SECONDS - age) * 1000)), confirm)
        self.click_timers.add(timer)

    def cancel_erase(self):
        # A released X may still be checking whether the editor is empty.
        if self.erase_serial is not None or self.last_down:
            self.CancelRepeat()
        self.erase_serial = None
        self.desktop_erase_until = 0.0

    def stop_input(self):
        self.cancel_erase()
        if self.last_frame is not None:
            self.Frame(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        self.last_frame = self.last_down = None
        self.recent_frames.clear()
        self.force_samples = [None, None]
        for timer in self.click_timers:
            GLib.source_remove(timer)
        self.click_timers.clear()
        self.ready = False
        self.verified_pin = None
        self.route_until = 0.0

    def can_route(self):
        return self.ready or time.monotonic() < self.route_until

    def acquiring_from_desktop(self):
        # KWin can show the panel before the first profile activation finishes.
        # A fresh X in this interval still belongs to the verified desktop
        # keyboard binding or to the very helper being acquired.
        if self.job is None or not self.steam_pid:
            return False
        try:
            index, _ = mapping.desktop_source()
            return (self.steam_pid == mapping.steam_pid() and not mapping.running_game()
                    and (mapping.active_app(index) == mapping.DESKTOP or
                         mapping.valid({"index": index, "pid": self.steam_pid})))
        except (OSError, RuntimeError, ValueError):
            return False

    def recovery_editor(self):
        state = self.chord._state()
        return all(state.get(k) for k in ("visible", "active", "available", "mode",
                                           "activeClientSupportsTextInput"))

    def reopen_browser_field(self):
        # The browser observer verifies the same field and a fresh pointer
        # return. Recheck the desktop session just before activating KWin.
        if (self.locked or self.in_game or self.fd is None or self.visible or
                self.chord.b or not self.chord.enabled):
            return False
        self.chord.poll_desktop_actions()
        try:
            if self.chord.client_ui or mapping.running_game():
                return False
        except OSError:
            return False
        state = self.chord._state()
        required = ("available", "mode", "activeClientSupportsTextInput")
        if state.get("visible") or not all(state.get(key) for key in required):
            return False
        self.chord._request(True)
        return True

    def editor_changed(self):
        if self.visible and not self.recovery_editor():
            self.PersonalChanged()
            self.cancel_erase()
            if not self.ready and self.verified_pin:
                self.stop_input()

    def close_device(self):
        self.field_return.cancel()
        self.panel_epoch += 1
        self.popup_guard.set_enabled(False)
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.chord.reset()
        self.stop_input()

    def read_reports(self, fd, condition):
        try:
            if condition & (GLib.IO_HUP | GLib.IO_ERR):
                raise OSError("Deck disconnected")
            for _ in range(64):
                try:
                    report = os.read(fd, 64)
                except BlockingIOError:
                    break
                if not report:
                    raise OSError("Deck disconnected")
                if len(report) != 64 or report[:4] != b"\x01\x00\x09\x40" or self.locked or self.in_game:
                    continue
                x_down = bool(report[8] & 0x40)
                guide_down = bool(report[9] & 0x20)
                b_down = bool(report[8] & 0x20)
                dismiss_was_held = self.chord.dismiss_b_held
                erase_edge = self.chord.update(guide_down, x_down, b_down, panel_visible=self.visible)
                if self.chord.dismiss_b_held:
                    if not dismiss_was_held:
                        self.stop_input()
                        self.field_return.arm()
                    continue
                if dismiss_was_held:
                    self.sync_profile()
                    continue
                if self.chord.client_ui:
                    if self.update_input_owner():
                        self.visibility()
                    continue
                held_erase = self.erase_serial is not None and self.erase_release_deadline == float("inf")
                if held_erase and guide_down:
                    self.cancel_erase()
                elif held_erase and not x_down:
                    # Codex can publish its empty-paragraph state after key-up.
                    self.erase_release_deadline = time.monotonic() + .4
                closing = (self.chord.stage or self.chord.armed) and not self.chord.want_open
                route_pads = self.can_route()
                if erase_edge:
                    self.erase_serial = None  # Supersede any delayed reply from the previous tap.
                    self.desktop_erase_until = 0.0
                    if not route_pads and self.visible and not closing and self.acquiring_from_desktop():
                        self.desktop_erase_until = time.monotonic() + OSK_INPUT_SECONDS
                if not self.visible or closing or (not route_pads and time.monotonic() >= self.desktop_erase_until):
                    self.cancel_erase()
                    self.last_down = None
                    continue
                touches = ((report[10] >> 3) & 3) if route_pads else 0
                clicks = ((report[10] >> 1) & 3) if route_pads else 0
                previous = self.last_down
                prior_triggers = self.held_triggers if previous is not None else 0
                self.held_triggers = trigger_mask(report, prior_triggers) if route_pads else 0
                # A trigger and a pad click share one press cycle per side;
                # only the actual trigger state controls its analog hysteresis.
                down = self.last_down = self.held_triggers | (clicks & touches)
                press = (down & ~previous) & touches if previous is not None else 0
                if x_down and not guide_down:
                    if erase_edge and self.recovery_editor():
                        press |= ERASE_BUTTON
                    if press & ERASE_BUTTON or held_erase:
                        down |= ERASE_BUTTON
                        self.last_down = down
                if press and not self.ready and not self.recovery_editor():
                    self.stop_input()
                    continue
                coords = struct.unpack_from("<hhhh", report, 16)
                raw_force = struct.unpack_from("<HH", report, 56)
                # The firmware decides the actual pad click. The lower threshold
                # provides tactile travel without a second keypress. Latching it
                # prevents pressure noise or holding the key from buzzing.
                prior_load = self.preloaded if previous is not None else 0
                self.preloaded = preload_mask(raw_force, touches, down, prior_load)
                preload = self.preloaded & ~prior_load & ~down if previous is not None else 0
                force = tuple(display_force(value) if touches & (1 << side) else 0
                              for side, value in enumerate(raw_force))
                frame = (*coords, touches, down & (touches | ERASE_BUTTON), *force) if touches or down & ERASE_BUTTON else (0,) * 8
                now = time.monotonic()
                for side, sample in enumerate(self.force_samples):
                    if sample is None:
                        continue
                    bit = 1 << side
                    if not clicks & touches & bit or press & bit or now - sample.time > PRESS_SAMPLE_SECONDS:
                        self.force_samples[side] = None
                    else:
                        sample.force[side] = max(sample.force[side], raw_force[side])
                elapsed = now - self.last_send
                # A language or symbols switch replaces the QML layout under
                # stationary fingers. Refresh the preview without another click.
                # Send every release, including a rapid lift/re-touch of the
                # same key. Rate-limit only movement and visual pressure.
                state_changed = self.last_frame is None or frame[4:6] != self.last_frame[4:6]
                if press or preload or state_changed or (frame != self.last_frame and elapsed >= 1 / 60) or ((touches or down & ERASE_BUTTON) and elapsed >= .1):
                    self.frame_serial = self.frame_serial % 0x3fffffff + 1
                    serial = self.frame_serial
                    if press & ERASE_BUTTON:
                        self.erase_serial = serial
                        self.erase_release_deadline = float("inf")
                    self.recent_frames.pop(serial - 48, None)
                    if serial == 1:
                        self.recent_frames.clear()
                    info = InputFrame(now, touches, press, down, preload, list(raw_force))
                    self.recent_frames[serial] = info
                    for side in (0, 1):
                        if press & clicks & (1 << side):
                            self.force_samples[side] = info
                    self.Frame(*frame[:5], press | (preload << 2), *frame[5:], serial)
                    self.last_send, self.last_frame = now, frame
        except OSError:
            self.close_device()
            self.watch = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def lock_changed(self, locked):
        self.panel_epoch += 1
        self.locked = bool(locked)
        self.PersonalChanged()
        if self.locked:
            self.field_return.cancel()
            self.popup_guard.set_enabled(False)
        self.chord.reset()
        self.sync_profile()

    def visibility(self):
        was_visible = self.visible
        try:
            state = self.props.GetAll(VK, timeout=.4)
            self.visible = bool(state.get("visible"))
        except dbus.DBusException:
            state = {}
            self.visible = False
        if self.visible and not was_visible:
            self.field_return.cancel()
        if self.visible != was_visible:
            self.panel_epoch += 1
            self.PersonalChanged()
        if self.visible and self.chord.client_ui and not self.locked:
            # A preceding KWin activation can finish after Steam took focus.
            try:
                self.chord._hide_panel()
            except dbus.DBusException:
                pass
        self.popup_guard.set_enabled(bool(self.chord.enabled and self.fd is not None
                                         and not self.locked and not self.in_game
                                         and state.get("available") and state.get("mode")))
        if not (self.locked or self.in_game):
            self.chord.reconcile(self.visible)
        self.sync_profile()

    def sync_profile(self):
        wanted = self.visible and self.fd is not None and not self.locked and not self.in_game
        if not wanted:
            self.stop_input()
        # Rebinding a still-held B to the desktop Escape/Backspace could send
        # it to the browser. Release the helper on key-up; a silent or detached
        # HID must not strand the mouse in the helper profile.
        if self.job is not None or (self.chord.dismiss_b_held and
                                    time.monotonic() - self.chord.last_dismiss_report < .4):
            return
        if wanted and not self.ready:
            if time.monotonic() < self.retry or (self.chord.stage and not self.chord.want_open):
                return
            stamp = self.chord.last_desktop_osk
            if stamp and time.monotonic() - stamp <= 4:
                action = ["enter-after-osk", str(stamp)]
            else:
                action = ["enter"]
        elif not wanted and (mapping.STATE.exists() or self.mouse_profile_stranded()):
            action = ["leave"]
        else:
            return
        p = subprocess.Popen([sys.executable, mapping.__file__, *action],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.job = p
        epoch = self.panel_epoch
        def complete():
            if p.poll() is None:
                return GLib.SOURCE_CONTINUE
            error = p.communicate()[1].strip()
            self.job = None
            if action[0].startswith("enter"):
                continuing = self.can_route()
                self.ready = (p.returncode == 0 and self.visible and not self.locked and not self.in_game
                              and self.panel_epoch == epoch)
                if self.ready:
                    try:
                        self.verified_pin = json.loads(mapping.STATE.read_text())
                    except (OSError, ValueError):
                        self.stop_input()
                    self.route_until = 0.0
                if error and error != self.enter_error:
                    log.info("%s", error)
                self.enter_error = error
                self.retry = time.monotonic() + ((.12 if action[0] == "enter-after-osk" else 1) if error else 0)
                if self.ready and not continuing:
                    self.last_down = self.last_frame = None
                    self.feedback.begin()
                    log.info("Deck input active for Plasma Keyboard")
            if not self.visible or self.locked:
                if not error:
                    self.sync_profile()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(40, complete)

    def mouse_profile_stranded(self):
        now = time.monotonic()
        if self.visible or self.locked or self.in_game or self.fd is None or self.chord.stage or now < self.next_mouse_check:
            return False
        self.next_mouse_check = now + .8
        return mapping.orphaned_index() is not None

    def poll_desktop_actions(self):
        self.chord.poll_desktop_actions(not (self.locked or self.game_running))
        if self.update_input_owner():
            self.visibility()
        stamp = self.chord.last_desktop_osk
        if stamp and self.visible and not self.locked and not self.in_game and time.monotonic() - stamp < 1.5:
            self.validate_profile()
            self.sync_profile()
        return GLib.SOURCE_CONTINUE

    def validate_profile(self):
        if not (self.ready or self.verified_pin):
            return
        try:
            pin = json.loads(mapping.STATE.read_text())
            if mapping.valid(pin):
                if self.verified_pin is not None and self.verified_pin != pin:
                    self.stop_input()
                    return
                self.ready = True
                self.route_until = 0.0
                self.verified_pin = pin
                return
            # Reacquire Steam Input independently. A desktop OSK briefly takes
            # controller profile 769 even when KWin keeps the same editor and
            # Plasma panel active. Route *live* edges only during that handoff.
            stamp = self.chord.last_desktop_osk
            now = time.monotonic()
            if (self.visible and not self.locked and not self.in_game and self.verified_pin == pin
                    and self.steam_pid == pin["pid"] == mapping.steam_pid()
                    and not mapping.running_game() and mapping.active_app(pin["index"]) == mapping.STEAM_UI
                    and mapping.selected_path(pin["index"], mapping.KEYBOARD) == mapping.TEMPLATE.resolve()):
                self.ready = False
                if (stamp is not None and 0 <= now - stamp < OSK_INPUT_SECONDS
                        and self.recovery_editor() and (self.chord.stock_open
                             or mapping.osk_displaced(pin["index"], stamp, True))):
                    self.route_until = stamp + OSK_INPUT_SECONDS
                    return
                # The focus notification can precede the OSK log by one poll.
                # Keep the verified ownership evidence, but allow no input.
                if now < self.chord.echo_until:
                    self.route_until = 0.0
                    return
        except (OSError, ValueError, KeyError, RuntimeError):
            pass
        self.stop_input()

    def update_input_owner(self):
        # Fullscreen Steam in KDE uses its own controller configuration just
        # as a game does, even when RunningAppID in the registry is zero.
        native = self.game_running or self.chord.client_ui
        if native == self.in_game:
            return False
        self.in_game = native
        self.panel_epoch += 1
        self.PersonalChanged()
        if native:
            self.field_return.cancel()
            self.chord.reset()
            self.stop_input()
            self.popup_guard.set_enabled(False)
        return True

    def maintenance(self):
        pid = mapping.steam_pid()
        try:
            self.game_running = bool(pid) and mapping.running_game()
        except OSError:
            self.game_running = True
        self.update_input_owner()
        if self.fd is not None and (pid != self.steam_pid or self.locked):
            if self.watch:
                GLib.source_remove(self.watch)
                self.watch = 0
            self.close_device()
        self.steam_pid = pid
        if pid and self.fd is None and not self.locked:
            try:
                path = deck_device()
                if path:
                    flags = os.O_NONBLOCK | os.O_CLOEXEC
                    try:
                        self.fd = os.open(path, os.O_RDWR | flags)
                    except PermissionError:
                        self.fd = os.open(path, os.O_RDONLY | flags)
                    self.watch = GLib.io_add_watch(self.fd, GLib.PRIORITY_DEFAULT,
                                                   GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP,
                                                   self.read_reports)
            except OSError:
                pass
        self.validate_profile()
        self.visibility()
        return GLib.SOURCE_CONTINUE


def main():
    if os.environ.get("XDG_SESSION_TYPE") != "wayland" or "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":"):
        return
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    DBusGMainLoop(set_as_default=True)
    loop = GLib.MainLoop()
    bridge = Bridge(dbus.SessionBus())
    def stop():
        loop.quit()
        return GLib.SOURCE_REMOVE
    GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    try:
        loop.run()
    finally:
        bridge.close_device()
        bridge.field_return.close()
        if bridge.job:
            bridge.job.wait(timeout=8)


if __name__ == "__main__":
    main()
