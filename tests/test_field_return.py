"""Cover delayed desktop accessibility replies and the KWin pointer observer."""
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
