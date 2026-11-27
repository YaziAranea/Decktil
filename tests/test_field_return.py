"""Cover delayed desktop accessibility replies and the KWin pointer observer."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import field_return


class FieldReturnTests(unittest.TestCase):
    def setUp(self):
        self.observer = object.__new__(field_return.BrowserFieldReturn)
        self.observer.generation = 0
        self.observer.armed = False
        self.observer.kwin_owner = ":1.2"
        self.observer.ticket = self.observer.field = self.observer.pending_open = None
        self.observer.source_dir = None
        self.observer.scripting = Mock()
        self.observer.bus = Mock()
        self.observer.bus.get_name_owner.return_value = ":1.2"
        self.observer.reopen = Mock(return_value=True)
        self.idle = self.enterContext(patch.object(field_return.GLib, "idle_add"))

    def test_empty_check_reads_only_owner_and_count_and_rejects_passwords(self):
        app, other, entry = (":1.3", "/app"), (":1.4", "/other"), (":1.3", "/edit")
        counts = []
        role = field_return.Atspi.Role.ENTRY
        found = [entry]

        def call(node, interface, method, signature="", args=()):
            if node[0] == "org.a11y.atspi.Registry":
                return [other, app]
            if method == "GetConnectionUnixProcessID":
                return {":1.3": 15, ":1.4": 4538125}[args[0]]
            if node == app and method == "GetMatches":
                flags = args[0][0][0] | (args[0][0][1] << 32)
                for flag in ("FOCUSED", "EDITABLE", "VISIBLE", "SHOWING", "SENSITIVE"):
                    self.assertTrue(flags & (1 << int(getattr(field_return.Atspi.StateType, flag))))
                return found
            if node == entry and method == "GetRole":
                return role
            if node == entry and method == "Get":
                self.assertEqual(args, ("org.a11y.atspi.Text", "CharacterCount"))
                counts.append(1)
                return count
            self.fail((node, interface, method))

        with patch.object(field_return, "accessibility_calls", side_effect=lambda *a, **kw: nullcontext(call)):
            count = 0
            self.assertTrue(field_return.focused_editor_empty(15, False))
            count = 1
            self.assertFalse(field_return.focused_editor_empty(15, False))
            self.assertTrue(field_return.focused_editor_empty(15, True))
            count = 2
            self.assertFalse(field_return.focused_editor_empty(15, True))
            role = field_return.Atspi.Role.PASSWORD_TEXT
            with self.assertRaises(ValueError):
                field_return.focused_editor_empty(15, True)
            found.clear()
            self.assertFalse(field_return.focused_editor_empty(15, False))
            self.assertTrue(field_return.focused_editor_empty(15, True))
        self.assertEqual(len(counts), 4)
        with patch.object(field_return, "accessibility_calls", return_value=nullcontext(None)):
            self.assertFalse(field_return.focused_editor_empty(15, False))
            self.assertTrue(field_return.focused_editor_empty(15, True))

    def test_codex_marker_fallback_applies_only_to_missing_accessibility(self):
        finished = Mock()
        with patch.object(field_return, "Thread") as worker:
            for error, marker, expected in ((field_return.dbus.DBusException(), True, True),
                                            (TimeoutError(), False, False),
                                            (ValueError(), True, False)):
                with patch.object(field_return, "focused_editor_empty", side_effect=error):
                    field_return.check_empty_async(15, marker, finished)
                    worker.call_args.kwargs["target"]()
                    self.idle.assert_called_with(finished, expected)

    def test_previous_dismissal_cannot_arm_open_or_end_the_current_one(self):
        observer = self.observer
        observer.arm()
        stale = observer.ticket
        reply = Mock()
        entry = ((":1.3", "/a"), (0, 0, 50, 10))
        with patch.object(field_return, "Thread") as thread, patch.object(field_return, "focused_field", return_value=entry):
            observer.FieldAt(stale, 2, -1, -1, reply, Mock(), sender=":1.2")
            thread.call_args.kwargs["target"]()
        finish, result = self.idle.call_args.args
        observer.arm()
        finish(result)
        reply.assert_called_once_with("")
        self.assertIsNone(observer.field)
        observer.pending_open = (observer.generation, float("inf"))
        observer.Open(stale, sender=":1.2")
        observer.End(stale, sender=":1.2")
        observer.Open(observer.ticket, sender=":1.9")
        observer.reopen.assert_not_called()
        self.assertTrue(observer.armed)
        observer.Open(observer.ticket, sender=":1.2")
        observer.reopen.assert_called_once()
        self.assertFalse(observer.armed)

    def test_shutdown_unloads_the_script_synchronously_with_no_main_loop(self):
        observer = self.observer
        observer.arm()
        ticket = observer.ticket
        with (tempfile.TemporaryDirectory() as runtime,
              patch.object(field_return.GLib, "get_user_runtime_dir", return_value=runtime),
              patch.object(field_return.dbus, "Interface")):
            observer.scripting.loadScript.return_value = 3
            observer._configure(observer.generation)
            script = Path(observer.scripting.loadScript.call_args.args[0])
            self.assertIn(ticket, script.read_text())
            observer.scripting.reset_mock()
            observer.close()
            self.assertFalse(script.exists())
            observer.scripting.unloadScript.assert_called_once_with(field_return.SCRIPT, timeout=.35)
            self.assertFalse(observer.armed)

    @unittest.skipUnless(Path("/usr/lib/qt6/bin/qml").is_file(), "Qt QML runner required")
    def test_kwin_script_ignores_stale_crossings_and_catches_fast_reentry(self):
        # Exercise the installed script source in the same Qt JavaScript engine
        # as KWin. Replies are delivered on demand to reproduce the races.
        source = field_return.SOURCE.read_text()
        qml = r"""
import QtQuick
Item {
    Component.onCompleted: {
        try {
            const w = {pid: 1, resourceClass: 'Chromium', clientGeometry: {x: 0, y: 0}};
            let move, activate, requests = [];
            const ws = {activeWindow:w, cursorPos:{x:5,y:5},
                        cursorPosChanged:{connect:cb=>move=cb}, windowActivated:{connect:cb=>activate=cb}};
            const call = (...args) => requests.push(args);
            const pop = (method) => {
                if (!requests.length || requests[0][3] !== method) throw Error('expected ' + method);
                return requests.shift();
            };
            const at = x => {ws.cursorPos.x = x; move()};
            const reply = msg => msg[msg.length-1]('0 0 20 20');
            const evaluate = new Function('workspace', 'callDBus', SOURCE);
            evaluate(ws, call);
            const first = pop('FieldAt');
            at(30); at(5);
            reply(first);
            const firstCrossing = pop('FieldAt');
            at(30); at(5);
            reply(firstCrossing);
            const nextCrossing = pop('FieldAt');
            reply(nextCrossing);
            pop('Open');
            at(30); at(5);
            const pending = pop('FieldAt');
            ws.activeWindow = null;
            activate(null);
            pop('End');
            reply(pending);
            if (requests.length) throw Error('stale action after deactivation');
            Qt.exit(0);
        } catch (error) {
            console.error(error);
            Qt.exit(2);
        }
    }
}
""".replace("SOURCE", json.dumps(source))
        with tempfile.TemporaryDirectory() as scratch:
            fixture = Path(scratch) / "field.qml"
            fixture.write_text(qml)
            result = subprocess.run(["/usr/lib/qt6/bin/qml", "-platform", "offscreen", str(fixture)],
                                    env={**os.environ, "QT_QUICK_BACKEND": "software", "QML_DISABLE_DISK_CACHE": "1"},
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=8)
            self.assertEqual(result.returncode, 0, result.stdout)
