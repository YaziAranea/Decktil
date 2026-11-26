"""Read-only check for a competing Gamescope Xwayland external overlay.

Gamescope picks a single external overlay from the root Xwayland server.
Even an unmapped window can reserve the plane, and a connector-specific
Mango HUD may have priority even at zero opacity. Inspect the same top-level
windows and leave every other claimant alone, regardless of class or name.
"""
from contextlib import contextmanager
import ctypes as C
import os
import re
import struct


def _binding(library, name, result, *args):
    method = getattr(library, name)
    method.argtypes = args
    method.restype = result
    return method


# The SteamOS image includes XCB without Python X11 bindings. Each cookie here
# is the single unsigned XCB request sequence. Replies/error objects use free().
_pointer = C.c_void_p
_uint = C.c_uint32
_xcb = C.CDLL("libxcb.so.1")
_connect = _binding(_xcb, "xcb_connect", _pointer, C.c_char_p, C.c_void_p)
_disconnect = _binding(_xcb, "xcb_disconnect", None, _pointer)
_failed = _binding(_xcb, "xcb_connection_has_error", C.c_int, _pointer)
_query = _binding(_xcb, "xcb_query_tree", _uint, _pointer, _uint)
_atom = _binding(_xcb, "xcb_intern_atom", _uint, _pointer, C.c_uint8, C.c_uint16, C.c_char_p)
_property = _binding(_xcb, "xcb_get_property", _uint, _pointer, C.c_uint8, _uint, _uint, _uint, _uint, _uint)
_attributes = _binding(_xcb, "xcb_get_window_attributes", _uint, _pointer, _uint)
_reply_attributes = _binding(_xcb, "xcb_get_window_attributes_reply", _pointer, _pointer, _uint, _pointer)
_reply_tree = _binding(_xcb, "xcb_query_tree_reply", _pointer, _pointer, _uint, _pointer)
_reply_atom = _binding(_xcb, "xcb_intern_atom_reply", _pointer, _pointer, _uint, _pointer)
_reply_property = _binding(_xcb, "xcb_get_property_reply", _pointer, _pointer, _uint, _pointer)
_free = _binding(C.CDLL(None), "free", None, _pointer)
_name = b"GAMESCOPE_EXTERNAL_OVERLAY"


@contextmanager
def _reply(function, connection, cookie):
    ptr = function(connection, cookie, None)
    if not ptr:
        raise ValueError("Xwayland could not answer the overlay check")
    try:
        yield ptr
    finally:
        _free(ptr)


def _hidden_mango(connection, xid):
    # The shipped MangoApp explicitly releases the plane while hidden. Some
    # other toolkits can retain a painted surface after clearing the same flag.
    process_name = b"_NET_WM_PID"
    lookup = _atom(connection, 1, len(process_name), process_name)
    attrs = _attributes(connection, xid)
    with _reply(_reply_atom, connection, lookup) as reply:
        pid_atom = _uint.from_address(reply + 8).value
    if not pid_atom:
        return False
    pid_read = _property(connection, 0, xid, pid_atom, 6, 0, 1)
    with _reply(_reply_attributes, connection, attrs) as reply:
        if C.c_uint8.from_address(reply + 26).value != 0:  # XCB_MAP_STATE_UNMAPPED
            return False
    with _reply(_reply_property, connection, pid_read) as reply:
        if (struct.unpack("=III", C.string_at(reply + 8, 12)) != (6, 0, 1)
                or C.c_uint8.from_address(reply + 1).value != 32):
            return False
        pid = _uint.from_address(reply + 32).value
    return os.path.samefile(f"/proc/{pid}/exe", "/usr/bin/mangoapp")


def available(own_xid):
    """Return True only for a live, top-level panel with no competing claimant."""
    display = os.environ.get("DISPLAY", "")
    if (not os.environ.get("GAMESCOPE_WAYLAND_DISPLAY") or not re.fullmatch(r":[0-9]+(?:\.0)?", display)
            or type(own_xid) is not int or not 0 < own_xid <= 0xffffffff):
        return False
    connection = _connect(display.encode(), None)
    if not connection:
        return False
    try:
        if _failed(connection):
            return False
        with _reply(_reply_tree, connection, _query(connection, own_xid)) as tree:
            root, parent = struct.unpack("=II", C.string_at(tree + 8, 8))
        if not root or root != parent:
            return False
        query = _query(connection, root)
        find_atom = _atom(connection, 1, len(_name), _name)  # only_if_exists
        with _reply(_reply_tree, connection, query) as tree:
            count = C.c_uint16.from_address(tree + 16).value
            if not 1 <= count <= 512:
                return False
            windows = list((_uint * count).from_address(tree + 32))
        if own_xid not in windows:
            return False
        with _reply(_reply_atom, connection, find_atom) as reply:
            atom = _uint.from_address(reply + 8).value
        if not atom:
            return True
        # Send all property reads before the first reply: one local server round
        # trip per batch instead of spawning a process for each window.
        queries = [(xid, _property(connection, 0, xid, atom, 0, 0, 1)) for xid in windows if xid != own_xid]
        for xid, query in queries:
            with _reply(_reply_property, connection, query) as prop:
                kind, remainder, items = struct.unpack("=III", C.string_at(prop + 8, 12))
                if not kind:
                    continue
                # A malformed property is not sufficient evidence that the slot is free.
                if (kind != 6 or C.c_uint8.from_address(prop + 1).value != 32
                        or remainder or items != 1 or _uint.from_address(prop + 32).value
                        or not _hidden_mango(connection, xid)):
                    return False
        return True
    except (OSError, ValueError):
        return False
    finally:
        _disconnect(connection)
