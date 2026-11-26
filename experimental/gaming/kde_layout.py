"""Temporarily select an installed KDE keyboard layout on the user's explicit request."""
from collections import deque
import fcntl
import os
import stat

from gi.repository import Gio, GLib


INTERFACE = "org.kde.KeyboardLayouts"
LOCALES = {"ru": "ru_RU", "us": "en_US"}
SYNC_LOCK = "decktil-bigpicture-layout.lock"


class _KWinLayouts:
    def __init__(self, changed):
        flags = Gio.DBusProxyFlags.DO_NOT_AUTO_START | Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES
        self.proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, flags, None, "org.kde.keyboard", "/Layouts", INTERFACE, None)
        self.handler = self.proxy.connect("g-signal", self._signal, changed)
        self.owner_handler = self.proxy.connect("notify::g-name-owner", lambda *_: changed(None))

    def _signal(self, proxy, sender, name, args, changed):
        if name == "layoutChanged":
            changed(args.unpack()[0])
        elif name == "layoutListChanged":
            changed(None)

    def _call(self, method, args=None):
        result = self.proxy.call_sync(method, args, Gio.DBusCallFlags.NO_AUTO_START, 250, None)
        return result.unpack()[0]

    def state(self):
        groups = tuple(tuple(row) for row in self._call("getLayoutsList"))
        index = self._call("getLayout")
        if not 0 <= index < len(groups):
            raise ValueError("KDE keyboard group unavailable")
        return groups, index

    def set(self, index):
        return self._call("setLayout", GLib.Variant("(u)", (index,)))

    def close(self):
        self.proxy.disconnect(self.handler)
        self.proxy.disconnect(self.owner_handler)


class KdeLayout:
    """Track an explicit panel language switch for one foreground Steam window.

    Reading the current locale never switches the keyboard. An external change,
    a replaced layout list, or loss of the original window cancels restoration.
    """
    def __init__(self, guard, *, backend=_KWinLayouts):
        self.guard = guard
        self.original = None
        self.owned = None
        self.expected = deque()
        self.sync_fd = None
        try:
            self.backend = backend(self._changed)
        except GLib.Error:
            self.backend = None
        if self.backend is not None:
            self._lock_sync()

    def _lock_sync(self):
        if self.sync_fd is not None:
            return
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if not runtime or not os.path.isabs(runtime):
            return
        fd = None
        try:
            directory = os.open(runtime, os.O_DIRECTORY | os.O_CLOEXEC | os.O_RDONLY)
            try:
                info = os.fstat(directory)
                if info.st_uid != os.getuid() or info.st_mode & 0o077:
                    return
                flags = os.O_CREAT | os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
                fd = os.open(SYNC_LOCK, flags, 0o600, dir_fd=directory)
            finally:
                os.close(directory)
            info = os.fstat(fd)
            if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid():
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.sync_fd, fd = fd, None
        except OSError:
            # The helper may already be finishing a sync. The next layout read
            # or explicit selection retries without blocking the HID reader.
            pass
        finally:
            if fd is not None:
                os.close(fd)

    def _unlock_sync(self):
        if self.sync_fd is not None:
            # Keep the inode: replacing it would let the two users lock different files.
            os.close(self.sync_fd)
            self.sync_fd = None

    def _forget(self):
        self.original = self.owned = None
        self.expected.clear()

    def _changed(self, index):
        # GLib can deliver our own signal after a subsequent method call.
        if self.expected and self.expected[0] == index:
            self.expected.popleft()
        else:
            self._forget()

    def _state(self):
        if self.backend is None:
            return None
        self._lock_sync()
        try:
            state = self.backend.state()
        except (GLib.Error, ValueError):
            self._forget()
            self._unlock_sync()
            return None
        groups, active = state
        if self.original is not None and (self.original[0] != groups or active != self.owned):
            self._forget()
        return state

    def locale(self):
        state = self._state()
        if state is None:
            return None
        groups, active = state
        return LOCALES.get(groups[active][0])

    def select(self, locale):
        """Apply a language-key choice. Never guess indices or change an unavailable layout."""
        state = self._state()
        if locale not in LOCALES.values() or state is None or self.sync_fd is None or not self.guard():
            return False
        groups, active = state
        if LOCALES.get(groups[active][0]) == locale:
            return True
        matches = [n for n, group in enumerate(groups) if LOCALES.get(group[0]) == locale]
        if len(matches) != 1:
            return False
        dest, = matches
        try:
            self.expected.append(dest)
            if not self.backend.set(dest):
                if self.expected and self.expected[-1] == dest:
                    self.expected.pop()
                return False
            if self.backend.state() != (groups, dest):
                self._forget()
                self._unlock_sync()
                return False
        except (GLib.Error, ValueError):
            self._forget()
            self._unlock_sync()
            return False
        if self.original is None:
            self.original = (groups, active)
        self.owned = dest
        return True

    def close(self):
        if self.backend is None:
            return
        try:
            if self.original is not None:
                state = self._state()
                if self.original is not None and state != self.original and self.guard():
                    self.backend.set(self.original[1])
        except (GLib.Error, ValueError):
            pass
        finally:
            self._forget()
            try:
                self.backend.close()
            finally:
                self.backend = None
                self._unlock_sync()
