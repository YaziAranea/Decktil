"""Suppress Steam's desktop keyboard while the Plasma input bridge owns the shortcut."""
import logging
from pathlib import Path
import time

import dbus

SCRIPT = "deck-plasma-keyboard-popup"
SOURCE = Path(__file__).with_name("suppress-steam-osk.js")
log = logging.getLogger(__name__)


class SteamPopupGuard:
    def __init__(self, bus):
        self.bus = bus
        self.scripting = self.interface("/Scripting", "org.kde.kwin.Scripting")
        self.active = None  # Reconcile a script left by an interrupted process.
        self.retry_at = 0
        bus.add_signal_receiver(self.owner_changed, signal_name="NameOwnerChanged",
                                dbus_interface="org.freedesktop.DBus", arg0="org.kde.KWin")

    def interface(self, path, name):
        return dbus.Interface(self.bus.get_object("org.kde.KWin", path, introspect=False), name)

    def owner_changed(self, name, old, new):
        self.active = None
        self.retry_at = 0

    def set_enabled(self, enabled):
        if self.active == enabled or time.monotonic() < self.retry_at:
            return
        try:
            if self.scripting.isScriptLoaded(SCRIPT, timeout=1):
                self.scripting.unloadScript(SCRIPT, timeout=1)
            if enabled:
                index = self.scripting.loadScript(str(SOURCE), SCRIPT, timeout=1)
                if index < 0:
                    raise RuntimeError("KWin could not load the Steam keyboard window filter")
                self.interface(f"/Scripting/Script{index}", "org.kde.kwin.Script").run(timeout=1)
            self.active = enabled
        except (dbus.DBusException, RuntimeError) as error:
            self.active = None
            self.retry_at = time.monotonic() + 2
            log.warning("Steam keyboard window filter: %s", error)
