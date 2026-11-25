#!/usr/bin/python3
"""Route Deck pad positions to the open Plasma input method in Desktop Mode."""
from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib, GLibUnix

from chord import SteamChord
from haptics import TrackpadFeedback
from popup import SteamPopupGuard
import profile as mapping

INTERFACE = "local.deck.PlasmaKeyboard"
VK = "org.kde.kwin.VirtualKeyboard"
log = logging.getLogger("deck-plasma-keyboard")
PRESS_SAMPLE_SECONDS = .008
OSK_INPUT_SECONDS = 1.2
ERASE_BUTTON = 16  # Frame bit reserved for physical B.


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
        self.physical_b = None
        self.erase_serial = None
        self.erase_release_deadline = 0.0
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
        self.in_game = False
        self.maintenance()
        GLib.timeout_add(45, self.poll_desktop_actions)
        GLib.timeout_add(900, self.maintenance)

    @dbus.service.signal(INTERFACE, signature="iiiiiiiiii")
    def Frame(self, lx, ly, rx, ry, touches, pressed, held, left_force, right_force, serial):
        """Positions, contacts, edges (low bits click, next two preload), held, pressure, serial."""

    @dbus.service.signal(INTERFACE, signature="")
    def CancelRepeat(self):
        """Cancel an active erase gesture on Wayland editor deactivation."""

    @dbus.service.method(INTERFACE, in_signature="i", out_signature="")
    def CloseOnEmpty(self, serial):
        # The layout sees the editor's surrounding/selected/preedit text. The
        # bridge owns KWin activation and can authenticate the current B edge.
        if (self.erase_serial != int(serial) or not self.visible or not self.can_route()
                or self.locked or self.in_game or time.monotonic() > self.erase_release_deadline
                or not self.recovery_editor()):
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
        # A released B may still be checking whether the editor is empty.
        if self.erase_serial is not None or self.last_down:
            self.CancelRepeat()
        self.erase_serial = None

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

    def recovery_editor(self):
        state = self.chord._state()
        return all(state.get(k) for k in ("visible", "active", "available", "mode",
                                           "activeClientSupportsTextInput"))

    def editor_changed(self):
        if self.visible and not self.recovery_editor():
            self.cancel_erase()
            if not self.ready and self.verified_pin:
                self.stop_input()

    def close_device(self):
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
                b = bool(report[8] & 0x20)
                previous_b, self.physical_b = self.physical_b, b
                if previous_b and not b:
                    # The QML reply can arrive just after a quick release.
                    self.erase_release_deadline = time.monotonic() + .25
                self.chord.update(bool(report[9] & 32), bool(report[8] & 64))
                closing = (self.chord.stage or self.chord.armed) and not self.chord.want_open
                if not self.can_route() or not self.visible or closing:
                    self.cancel_erase()
                    self.last_down = None
                    continue
                touches = (report[10] >> 3) & 3
                clicks = (report[10] >> 1) & 3
                previous = self.last_down
                self.held_triggers = trigger_mask(report, self.held_triggers if previous is not None else 0)
                # A trigger and a pad click share one press cycle per side;
                # only the actual trigger state controls its analog hysteresis.
                down = self.last_down = self.held_triggers | (clicks & touches)
                press = (down & ~previous) & touches if previous is not None else 0
                if b:
                    # Remove just the stock Escape binding from the helper
                    # layout. Leave a user's custom B action entirely to Steam.
                    b_edge = (previous_b is False and self.ready and not report[9] & 32
                              and self.chord.desktop_b_closes_keyboard() and self.recovery_editor())
                    if b_edge or (previous is not None and previous & ERASE_BUTTON):
                        down |= ERASE_BUTTON
                        self.last_down = down
                        if b_edge:
                            press |= ERASE_BUTTON
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
                frame = (*coords, touches, down & (touches | ERASE_BUTTON), *force) if touches or b else (0,) * 8
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
        if self.locked:
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
        if self.visible != was_visible:
            self.panel_epoch += 1
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
        if self.job is not None:
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
        self.chord.poll_desktop_actions(not (self.locked or self.in_game))
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

    def maintenance(self):
        pid = mapping.steam_pid()
        was_in_game = self.in_game
        try:
            self.in_game = bool(pid) and mapping.running_game()
        except OSError:
            self.in_game = True
        if self.in_game and not was_in_game:
            self.chord.reset()
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
        if bridge.job:
            bridge.job.wait(timeout=8)


if __name__ == "__main__":
    main()
