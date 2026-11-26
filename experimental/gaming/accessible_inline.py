"""Observe a known Steam library search through the accessibility bus."""
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
