"""Bounded client for Gamescope's private UTF-8 input-method protocol (versions 1 and 2).

The Deck ships libwayland but no Python bindings for this compositor extension.
Only a registry, one seat, the input-method manager and sync callbacks are bound.
These interfaces do not send file descriptors to this client.
"""
import os
from pathlib import Path
import socket
import struct
import time


class UnmappedKey(ValueError):
    """The current keyboard map cannot produce the selected symbol."""


# Use 3 for the registry sync, then reuse 7 only after a sync callback is deleted.
DISPLAY, REGISTRY, SEAT, MANAGER, INPUT_METHOD = 1, 2, 4, 5, 6
ACTIONS = {"enter": 1, "backspace": 2, "delete": 3, "left": 4, "right": 5,
           "up": 6, "down": 7}
WORD = struct.Struct("=I")
HEADER = struct.Struct("=II")


def _string(encoded):
    size = len(encoded) + 1
    return WORD.pack(size) + encoded + bytes(1 + (-size % 4))


def _request(object_id, opcode, payload):
    return HEADER.pack(object_id, ((8 + len(payload)) << 16) | opcode) + payload


class GamescopeOutput:
    """Send text to the focused Gamescope client while a target guard still matches."""

    def __init__(self):
        display = os.environ.get("GAMESCOPE_WAYLAND_DISPLAY")
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if not display or not runtime:
            raise RuntimeError("No Gamescope session")
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.settimeout(.7)
        self._globals = {}
        self._serial = None
        try:
            self._socket.connect(os.fspath(Path(runtime) / display))
            self._socket.sendall(_request(DISPLAY, 1, WORD.pack(REGISTRY)))
            self._sync(callback=3)
            for interface, object_id in (("wl_seat", SEAT), ("gamescope_input_method_manager", MANAGER)):
                if interface not in self._globals:
                    raise RuntimeError("Gamescope is missing " + interface)
                name, version = self._globals[interface]
                version = min(version, 2 if object_id == MANAGER else 1)
                if object_id == MANAGER:
                    self.version = version
                self._socket.sendall(_request(REGISTRY, 0, WORD.pack(name) + _string(interface.encode())
                                              + WORD.pack(version) + WORD.pack(object_id)))
            self._socket.sendall(_request(MANAGER, 1, WORD.pack(SEAT) + WORD.pack(INPUT_METHOD)))
            self._sync()
            if self._serial is None:
                raise RuntimeError("Gamescope input method did not become ready")
        except BaseException:
            self.close()
            raise

    def close(self):
        self._socket.close()

    def _read(self, size, deadline):
        data = bytearray()
        while len(data) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Gamescope stopped acknowledging input")
            self._socket.settimeout(remaining)
            block = self._socket.recv(size - len(data))
            if not block:
                raise ConnectionError("Gamescope closed the input connection")
            data.extend(block)
        return bytes(data)

    def _sync(self, callback=7):
        self._socket.sendall(_request(DISPLAY, 0, WORD.pack(callback)))
        done = False
        deadline = time.monotonic() + .7
        while True:
            obj, header = HEADER.unpack(self._read(8, deadline))
            size, opcode = header >> 16, header & 0xffff
            if size < 8 or size % 4:
                raise ConnectionError("Invalid Wayland event size")
            body = self._read(size - 8, deadline)
            if obj == DISPLAY and opcode == 0:
                raise ConnectionError("Gamescope rejected the input protocol")
            if obj == REGISTRY and opcode == 0:
                name, length = HEADER.unpack_from(body)
                interface = body[8:8 + length - 1].decode("ascii")
                self._globals[interface] = (name, WORD.unpack_from(body, 8 + ((length + 3) & ~3))[0])
            elif obj == INPUT_METHOD:
                if opcode == 0:
                    raise RuntimeError("Gamescope input method unavailable")
                if opcode == 1:
                    self._serial = WORD.unpack(body)[0]
            elif obj == callback and opcode == 0:
                done = True
            elif obj == DISPLAY and opcode == 1 and WORD.unpack(body)[0] == callback:
                if not done:
                    raise ConnectionError("Gamescope discarded the sync callback")
                self._socket.settimeout(.7)
                return

    def _commit(self, opcode, payload, guard):
        if not guard():
            return False
        self._socket.sendall(_request(INPUT_METHOD, opcode, payload)
                             + _request(INPUT_METHOD, 1, WORD.pack(self._serial)))
        self._sync()
        return True

    def text(self, value, guard):
        encoded = value.encode("utf-8")
        if not encoded or b"\0" in encoded or len(encoded) > 1024:
            raise ValueError("Invalid keyboard text length or embedded NUL")
        return self._commit(2, _string(encoded), guard)

    def action(self, name, guard):
        code = ACTIONS[name]
        if code >= 6 and self.version < 2:
            raise ValueError("Vertical navigation requires Gamescope input method version 2")
        return self._commit(3, WORD.pack(code), guard)
