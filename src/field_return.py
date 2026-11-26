"""Reopen a dismissed desktop keyboard when the pointer returns to its browser editor."""
from configparser import ConfigParser, Error as ConfigError
from pathlib import Path
from secrets import token_hex
from tempfile import TemporaryDirectory
from threading import Thread
import time

import dbus
import dbus.service
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi, GLib


INTERFACE = "local.deck.PlasmaKeyboard"
SCRIPT = "decktil-browser-field-return"
SOURCE = Path(__file__).with_name("browser-field-return.js")
ACCESSIBLE = "org.a11y.atspi.Accessible"
ROOT = "/org/a11y/atspi/accessible/root"


def flatpak_instance(pid):
    metadata = ConfigParser(interpolation=None, strict=False)
    try:
        metadata.read(f"/proc/{pid}/root/.flatpak-info")
        return metadata["Application"]["name"], metadata["Instance"]["instance-id"]
    except (ConfigError, KeyError, OSError):
        return None


def same_browser(pid, owner):
    if owner == pid:
        return True
    # Flatpak proxies the accessibility bus. Match this exact sandbox instance.
    try:
        if Path(f"/proc/{owner}/comm").read_text().strip() != "xdg-dbus-proxy":
            return False
    except OSError:
        return False
    identity = flatpak_instance(pid)
    return identity is not None and flatpak_instance(owner) == identity


def focused_field(pid):
    """Read only the owner, state, role and window-relative bounds of the focused editor."""
    deadline = time.monotonic() + .85
    session = dbus.SessionBus(private=True)
    access = None
    try:
        address = session.call_blocking("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus",
                                        "GetAddress", "", (), timeout=.25)
        access = dbus.bus.BusConnection(address)

        def call(node, interface, method, signature="", args=()):
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError
            return access.call_blocking(*node, interface, method, signature, args, timeout=min(.3, left))

        apps = call(("org.a11y.atspi.Registry", ROOT), ACCESSIBLE, "GetChildren")
        for app in apps:
            if not app[0].startswith(":"):
                continue
            owner = int(call(("org.freedesktop.DBus", "/org/freedesktop/DBus"), "org.freedesktop.DBus",
                             "GetConnectionUnixProcessID", "s", (app[0],)))
            if not same_browser(pid, owner):
                continue
            flags = (Atspi.StateType.FOCUSED, Atspi.StateType.EDITABLE, Atspi.StateType.VISIBLE,
                     Atspi.StateType.SHOWING, Atspi.StateType.SENSITIVE)
            state = sum(1 << int(flag) for flag in flags)
            rule = ([state & 0xffffffff, state >> 32], 1, {}, 1, [0, 0, 0, 0], 1, [], 1, False)
            found = call(app, "org.a11y.atspi.Collection", "GetMatches", "(aiia{ss}iaiiasib)uib",
                         (rule, 1, 2, True))
            if len(found) != 1 or found[0][0] != app[0]:
                continue
            entry = found[0]
            role = int(call(entry, ACCESSIBLE, "GetRole"))
            if role not in (Atspi.Role.ENTRY, Atspi.Role.TEXT):
                continue
            rect = tuple(map(int, call(entry, "org.a11y.atspi.Component", "GetExtents",
                                       "u", (int(Atspi.CoordType.WINDOW),))))
            if rect[2] > 0 and rect[3] > 0:
                return tuple(entry), rect
        return None
    finally:
        if access is not None:
            access.close()
        session.close()


class BrowserFieldReturn(dbus.service.Object):
    def __init__(self, bus, reopen):
        super().__init__(bus, "/BrowserFieldReturn")
        self.bus = bus
        self.reopen = reopen
        self.generation = 0
        self.armed = False
        self.field = None
        self.kwin_owner = None
        self.pending_open = None
        self.ticket = None
        self.source_dir = None
        self.scripting = dbus.Interface(bus.get_object("org.kde.KWin", "/Scripting", introspect=False),
                                        "org.kde.kwin.Scripting")
        GLib.idle_add(self._configure, self.generation)

    def arm(self):
        self.generation += 1
        self.armed = True
        self.field = None
        self.ticket = token_hex(16)
        self.pending_open = None
        GLib.idle_add(self._configure, self.generation)

    def cancel(self):
        if self.armed:
            self.generation += 1
            self.armed = False
            self.field = None
            self.ticket = self.pending_open = None
            GLib.idle_add(self._configure, self.generation)

    def close(self):
        # The GLib loop has already stopped during normal service shutdown.
        self.generation += 1
        self.armed = False
        self.ticket = self.field = self.pending_open = None
        self._configure(self.generation)

    def _drop_source(self):
        if self.source_dir is not None:
            self.source_dir.cleanup()
            self.source_dir = None

    def _configure(self, generation):
        if self.generation != generation:
            return False
        try:
            self.kwin_owner = self.bus.get_name_owner("org.kde.KWin")
            if self.scripting.isScriptLoaded(SCRIPT, timeout=.35):
                self.scripting.unloadScript(SCRIPT, timeout=.35)
            self._drop_source()
            if self.armed:
                self.source_dir = TemporaryDirectory(prefix="decktil-field-", dir=GLib.get_user_runtime_dir())
                script = Path(self.source_dir.name) / SOURCE.name
                script.write_text(SOURCE.read_text().replace("@@TICKET@@", self.ticket))
                index = self.scripting.loadScript(str(script), SCRIPT, timeout=.35)
                if index < 0:
                    raise RuntimeError("Could not load the keyboard field observer")
                obj = self.bus.get_object("org.kde.KWin", f"/Scripting/Script{index}", introspect=False)
                dbus.Interface(obj, "org.kde.kwin.Script").run(timeout=.35)
        except (dbus.DBusException, OSError, RuntimeError):
            retry_unload = self.armed
            self.armed = False
            self.ticket = self.field = self.pending_open = None
            self._drop_source()
            if retry_unload:
                GLib.idle_add(self._configure, self.generation)
        return False

    @dbus.service.method(INTERFACE, in_signature="siii", out_signature="s", sender_keyword="sender",
                         async_callbacks=("reply", "error"))
    def FieldAt(self, ticket, pid, x, y, reply, error, sender=None):
        if not self.armed or sender != self.kwin_owner or ticket != self.ticket:
            reply("")
            return
        generation = self.generation
        initial = (x, y) == (-1, -1)

        def finish(field):
            result = ""
            if generation == self.generation and self.armed:
                if initial:
                    self.field = field
                    if field is not None:
                        result = " ".join(map(str, field[1]))
                elif field and self.field and field[0] == self.field[0]:
                    left, top, w, h = field[1]
                    if left <= x < left + w and top <= y < top + h:
                        self.pending_open = (generation, time.monotonic() + .4)
                        result = " ".join(map(str, field[1]))
            reply(result)
            return False

        def lookup():
            try:
                field = focused_field(int(pid))
            except (dbus.DBusException, TimeoutError, OSError, ValueError):
                field = None
            GLib.idle_add(finish, field)
        Thread(target=lookup, daemon=True).start()

    @dbus.service.method(INTERFACE, in_signature="s", out_signature="", sender_keyword="sender")
    def Open(self, ticket, sender=None):
        if not self.armed or sender != self.kwin_owner or ticket != self.ticket:
            return
        checked = self.pending_open
        self.pending_open = None
        if checked and checked[0] == self.generation and time.monotonic() <= checked[1] and self.reopen():
            self.cancel()

    @dbus.service.method(INTERFACE, in_signature="s", out_signature="", sender_keyword="sender")
    def End(self, ticket, sender=None):
        if self.armed and sender == self.kwin_owner and ticket == self.ticket:
            self.cancel()
