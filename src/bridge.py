#!/usr/bin/python3
"""Route Deck pad positions to the open Plasma input method in Desktop Mode."""
import json
import logging
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
import profile as mapping

INTERFACE = "local.deck.PlasmaKeyboard"
VK = "org.kde.kwin.VirtualKeyboard"
log = logging.getLogger("deck-plasma-keyboard")


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


class Bridge(dbus.service.Object):
    def __init__(self, bus):
        self.name = dbus.service.BusName(INTERFACE, bus, do_not_queue=True)
        super().__init__(self.name, "/Pads")
        self.chord = SteamChord(bus)
        self.props = self.chord.properties
        self.fd = None
        self.steam_pid = None
        self.watch = 0
        self.visible = False
        self.locked = False
        self.ready = False
        self.job = None
        self.retry = 0.0
        self.enter_error = ""
        self.last_frame = None
        self.last_send = 0.0
        self.last_down = None
        self.held_triggers = 0
        self.feedback = TrackpadFeedback()
        self.frame_serial = 0
        self.recent_frames = {}
        bus.add_signal_receiver(self.visibility, signal_name="visibleChanged",
                                dbus_interface=VK, bus_name="org.kde.KWin", path="/VirtualKeyboard")
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
        GLib.timeout_add(90, lambda: self.chord.poll_desktop_actions(not (self.locked or self.in_game)))
        GLib.timeout_add(900, self.maintenance)

    @dbus.service.signal(INTERFACE, signature="iiiiiiiiii")
    def Frame(self, lx, ly, rx, ry, touches, pressed, held, left_force, right_force, serial):
        """Current positions, edges, held state, scaled pressure and frame identifier."""

    @dbus.service.method(INTERFACE, in_signature="iii", out_signature="")
    def Feedback(self, serial, clicked, hovered):
        # Confirm only the edges that this layout actually resolved to enabled keys.
        # Drop duplicate or late replies, especially across a hide or layout switch.
        frame = self.recent_frames.pop(int(serial), None)
        if not frame or not self.ready or not self.visible or self.locked or self.in_game:
            return
        when, touches, pressed, held = frame
        if time.monotonic() - when <= .3:
            self.feedback.play(self.fd, clicked & pressed, hovered & touches & ~held)

    def stop_input(self):
        if self.last_frame is not None:
            self.Frame(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        self.last_frame = self.last_down = None
        self.recent_frames.clear()
        self.ready = False

    def close_device(self):
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
                self.chord.update(bool(report[9] & 32), bool(report[8] & 64))
                if not self.ready or not self.visible or self.chord.stage or self.chord.armed:
                    self.last_down = None
                    continue
                touches = (report[10] >> 3) & 3
                clicks = (report[10] >> 1) & 3
                previous = self.last_down
                self.held_triggers = trigger_mask(report, self.held_triggers if previous is not None else 0)
                # A trigger and a pad click share one press cycle per side;
                # only the actual trigger state controls its analog hysteresis.
                down = self.last_down = self.held_triggers | clicks
                press = (down & ~previous) & touches if previous is not None else 0
                coords = struct.unpack_from("<hhhh", report, 16)
                # The digital click determines selection; continuous force is visual only.
                raw_force = struct.unpack_from("<HH", report, 56)
                force = tuple(min(31, value // 1024) if touches & (1 << side) else 0
                              for side, value in enumerate(raw_force))
                frame = (*coords, touches, down & touches, *force) if touches else (0,) * 8
                now = time.monotonic()
                elapsed = now - self.last_send
                # A language or symbols switch replaces the QML layout under
                # stationary fingers. Refresh the preview without another click.
                if press or (frame != self.last_frame and elapsed >= 1 / 60) or (touches and elapsed >= .1):
                    self.frame_serial = self.frame_serial % 0x3fffffff + 1
                    serial = self.frame_serial
                    self.recent_frames.pop(serial - 48, None)
                    if serial == 1:
                        self.recent_frames.clear()
                    self.recent_frames[serial] = (now, touches, press, down)
                    self.Frame(*frame[:5], press, *frame[5:], serial)
                    self.last_send, self.last_frame = now, frame
        except OSError:
            self.close_device()
            self.watch = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def lock_changed(self, locked):
        self.locked = bool(locked)
        self.chord.reset()
        self.sync_profile()

    def visibility(self):
        try:
            self.visible = bool(self.props.Get(VK, "visible", timeout=.4))
        except dbus.DBusException:
            self.visible = False
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
            if time.monotonic() < self.retry or self.chord.stage:
                return
            action = "enter"
        elif not wanted and mapping.STATE.exists():
            action = "leave"
        else:
            return
        p = subprocess.Popen([sys.executable, mapping.__file__, action],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.job = p
        def complete():
            if p.poll() is None:
                return GLib.SOURCE_CONTINUE
            error = p.communicate()[1].strip()
            self.job = None
            if action == "enter":
                self.ready = p.returncode == 0
                self.last_down = self.last_frame = None
                if error and error != self.enter_error:
                    log.info("%s", error)
                self.enter_error = error
                self.retry = time.monotonic() + (1 if error else 0)
                if self.ready:
                    self.feedback.begin()
                    log.info("Deck input active for Plasma Keyboard")
            if not self.visible or self.locked:
                if not error:
                    self.sync_profile()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(40, complete)

    def maintenance(self):
        pid = mapping.steam_pid()
        try:
            self.in_game = bool(pid) and mapping.running_game()
        except OSError:
            self.in_game = True
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
        if self.ready:
            try:
                if not mapping.valid(json.loads(mapping.STATE.read_text())):
                    self.stop_input()
            except (OSError, ValueError, KeyError, RuntimeError):
                self.stop_input()
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
