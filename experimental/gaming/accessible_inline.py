"""Observe a known Steam library search through the accessibility bus."""
import time

import focus


class AccessibleInlineState:
    """Use on a single worker thread, after verifying foreground ownership."""

    def __init__(self):
        self.tree = None
        self.previous = None

    def sample(self, xid):
        previous, self.previous = self.previous, None
        if type(xid) is not int or not 0 < xid <= 0xffffffff:
            return None
        try:
            pid = int(focus._props(["-id", hex(xid)], ["_NET_WM_PID"])["_NET_WM_PID"], 0)
            if not focus._trusted(pid):
                return None
            # AT-SPI identifies the process. Require its visible Steam window
            # to be unambiguous before associating any field with this XID.
            windows = focus._command("xdotool", "search", "--all", "--onlyvisible", "--pid", str(pid),
                                     "--class", "^steam$").splitlines()
            if windows != [str(xid)]:
                return None
            skin = focus._known_skin()
            if not skin:
                return None
            if self.tree is None:
                self.tree = focus._Tree()
            tree = self.tree
            tree.start()
            field = tree.editor(xid, pid, skin)
            if field is None or not field.search:
                if field is None and tree.reason in ('editor-type-or-state', 'not-library-search'):
                    return 'other', None
                return None
            doc = (field.bus, field.document)
            # A newly opened Steam dialog can leave an old search focused in
            # the accessibility tree. Treat exposed sensitive UI as ambiguous.
            if any(tree.matches(doc, role=role, visible=True)
                   for role in (focus.ROLE.DIALOG, focus.ROLE.ALERT, focus.ROLE.PASSWORD_TEXT)):
                return None
            wrapper = previous[1] if previous and previous[0] == field else None
            inline, wrapper = tree.inline(field, wrapper, skin)
            if inline is True:
                visible = tree.matches(doc, visible=True, limit=257)
                if not visible or len(visible) > 256:
                    return None
                blocked = {skin.buffer, skin.modal, skin.standalone}
                for node in visible:
                    if node[0] != field.bus:
                        return None
                    classes = tree.call(node, focus.ACC, "GetAttributes").get("class", "").split()
                    if blocked.intersection(classes):
                        return None
                self.previous = (field, wrapper)
                state = 'inline'
            elif wrapper is not None:
                # Chromium can temporarily prune the wrapper from its visible
                # collection while its retained reference still reports showing.
                self.previous = (field, wrapper)
                return None
            elif tree.clear_to_wait(field, skin):
                state = 'editor'
            else:
                return None
            return state, ('atspi', xid, pid, field.bus, field.document, field.entry)
        except focus.ERRORS:
            self.tree = None
            return None


def recover_space(key, *, held):
    """Return focus only within the original accessible Steam search document."""
    _, xid, pid, bus, document, entry = key
    doc, original = (bus, document), (bus, entry)
    try:
        if (int(focus._props(["-id", hex(xid)], ["_NET_WM_PID"])["_NET_WM_PID"], 0) != pid
                or not focus._trusted(pid) or focus._command("xdotool", "getwindowfocus").strip() != str(xid)
                or focus._command("xdotool", "search", "--all", "--onlyvisible", "--pid", str(pid),
                                  "--class", "^steam$").splitlines() != [str(xid)]):
            return 'blocked'
        skin = focus._known_skin()
        if not skin:
            return 'blocked'
        tree = focus._Tree()
        tree.start()
        target = tree.editor(xid, pid, skin, expected_entry=original)
        if (target != focus.Editor(xid, pid, bus, document, entry, True)
                or not tree.clear_to_wait(target, skin)
                or any(tree.matches(doc, role=role, visible=True)
                       for role in (focus.ROLE.ALERT, focus.ROLE.PASSWORD_TEXT))):
            return 'blocked'
        focused = tree.matches(doc, focused=True, visible=True)
        if len(focused) != 1 or focused[0][0] != bus:
            return 'blocked'
        control, = focused
        if control == original:
            return 'focused'
        uri = tree.call(doc, focus.DOCUMENT, 'GetAttributeValue', 's', ['URI'])
        if not (focus._route(uri, True) and focus.urlsplit(uri).path.startswith('/routes/search/')):
            return 'blocked'
        role = tree.call(control, focus.ACC, 'GetRole')
        tab = role == focus.ROLE.PAGE_TAB
        result = (held and role == focus.ROLE.LINK and skin.search_result and skin.search_grid
                  and skin.search_result in tree.call(control, focus.ACC, 'GetAttributes').get('class', '').split())
        if not (tab or result):
            return 'blocked'
        # Chromium may insert generic accessibility containers between the
        # tab and its tab list, or a held press may reach a link in the grid.
        parent, grid, wrapper = control, False, False
        for _ in range(16):
            parent = tree.call(parent, 'org.freedesktop.DBus.Properties', 'Get', 'ss', [focus.ACC, 'Parent'])
            if parent[0] != bus or parent == doc or parent[1] == focus.NULL:
                return 'blocked'
            parent_role = tree.call(parent, focus.ACC, 'GetRole')
            if tab and parent_role == focus.ROLE.PAGE_TAB_LIST:
                break
            if result:
                attrs = tree.call(parent, focus.ACC, 'GetAttributes')
                grid |= parent_role == focus.ROLE.TABLE
                wrapper |= grid and skin.search_grid in attrs.get('class', '').split()
                if wrapper and 'tabpanel' in attrs.get('xml-roles', '').split():
                    break
        else:
            return 'blocked'
        component = 'org.a11y.atspi.Component'
        fx, fy, fw, fh = tree.call(original, component, 'GetExtents', 'u', [0])
        px, py, pw, ph = tree.call(parent, component, 'GetExtents', 'u', [0])
        if (min(fw, fh, pw, ph) <= 2 or abs(py - (fy + fh)) > fh
                or px >= fx + fw or px + pw <= fx):
            return 'blocked'
        tree.call(original, component, 'GrabFocus')
        # Chromium acknowledges the request before its renderer publishes the
        # new accessibility focus. Verify the same field before typing.
        deadline = time.monotonic() + .12
        while True:
            if tree.editor(xid, pid, skin) == target:
                return 'restored'
            if time.monotonic() >= deadline:
                break
            time.sleep(.01)
    except focus.ERRORS:
        pass
    return 'blocked'
