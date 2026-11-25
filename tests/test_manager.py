"""Exercise the real GTK manager privately, with a simulated local installer."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


BASE = Path(__file__).resolve().parents[1]
BACKEND = """import json, sys
from pathlib import Path
state_file = Path(__file__).with_name('state.json')
state = json.loads(state_file.read_text())
args = sys.argv[1:]
if args == ['status']:
    print(json.dumps(state))
elif args == ['prepare']:
    state.update(installation='managed', prepared=True, can_switch=True)
elif args == ['bind']:
    if not state_file.with_name('steam-closed').exists():
        sys.exit('Choose Steam > Exit and allow Steam to finish before offline binding')
elif len(args) == 2 and args[0] == 'mode':
    state.update(mode=args[1], setup_complete=True)
elif len(args) == 2 and args[0] == 'personal':
    if args[1] == 'clear':
        state['cleared'] = True
    else:
        state['personal_enabled'] = args[1] == 'on'
else:
    sys.exit('Unexpected command')
state_file.write_text(json.dumps(state))
"""
BUS = """<busconfig>
<type>session</type><listen>unix:tmpdir=/tmp</listen><auth>EXTERNAL</auth>
<policy context="default"><allow send_destination="*"/><allow receive_sender="*"/>
<allow own="*"/></policy></busconfig>"""


def private_session(root):
    sys.path.insert(0, str(BASE))
    import manager
    from gi.repository import Gio, GLib, Gtk

    manager.SETUP = root / "setup.py"
    app = Gtk.Application(application_id="local.deck.PrivateKeyboardTest",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)
    window = manager.KeyboardWindow(app)
    context = GLib.MainContext.default()

    def finish():
        deadline = time.monotonic() + 5
        while window.pending:
            assert time.monotonic() < deadline, "The installer callback did not finish"
            while context.pending():
                context.iteration(False)
            time.sleep(.005)

    def children(widget):
        yield widget
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                yield from children(child)

    def click(key):
        title = window.t(key)
        for button in children(window):
            if isinstance(button, Gtk.Button):
                name = button.get_accessible().get_name() or ""
                if name.split(" ·")[0] == title or button.get_label() == title:
                    assert button.is_sensitive(), title
                    button.clicked()
                    return
        raise AssertionError(title)

    finish()
    assert window.lang == 0 and window.state["installation"] == "missing"
    click("plasma")
    assert window.page == "prepare"
    click("prepare")
    assert window.pending
    window.language.set_active(1)
    finish()
    assert window.lang == 1 and window.page == "template" and window.state["prepared"]
    click("offline_link")
    click("bind")
    finish()
    assert window.page == "offline" and window.error[0] == "need_exit"
    (root / "steam-closed").touch()
    click("bind")
    finish()
    assert window.page == "launch"
    click("verify")
    finish()
    assert window.state["mode"] == "plasma" and window.page == "choose"
    click("steam")
    finish()
    assert window.state["mode"] == "steam"
    click("plasma")
    finish()
    assert window.state["mode"] == "plasma"
    assert manager.preferred_language() == 1
    click("personal")
    click("learn")
    finish()
    assert window.page == "personal" and window.state["personal_enabled"]
    click("clear_personal")
    click("cancel")
    assert not window.state.get("cleared")
    click("clear_personal")
    click("clear")
    finish()
    assert window.state["cleared"] and window.state["personal_enabled"]
    click("learn")
    finish()
    assert not window.state["personal_enabled"]
    click("back")
    from appearance_page import AppearancePage, rgba
    from src import appearance

    def page():
        return next(widget for widget in children(window) if isinstance(widget, AppearancePage))

    click("appearance")
    page().theme.set_active_id("lagoon")
    assert appearance.load()["preset"] == "default"
    page().apply.clicked()
    assert appearance.load()["preset"] == "lagoon"
    picker = page().pickers["keyStart"]
    picker.set_rgba(rgba("#7D31B7"))
    picker.emit("color-set")
    page().direction.set_active_id("horizontal")
    assert page().theme.get_active_id() == "custom"
    window.language.set_active(0)
    assert page().draft["keyStart"].lower() == "#7d31b7"
    page().apply.clicked()
    page().theme.set_active_id("default")
    page().apply.clicked()
    click("back")
    click("appearance")
    assert page().theme.get_active_id() == "default"
    page().theme.set_active_id("custom")
    assert page().draft["keyStart"].lower() == "#7d31b7" and page().draft["direction"] == "horizontal"
    click("back")
    assert appearance.load()["preset"] == "default"
    fixture = root / "state.json"
    state = json.loads(fixture.read_text())
    state["installation"] = "manual"
    fixture.write_text(json.dumps(state))
    click("refresh")
    finish()
    assert window.state["installation"] == "manual"
    click("steam")
    finish()
    assert window.state["mode"] == "steam" and window.page == "choose"
    window.destroy()


class NativeManager(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("gi") and shutil.which("dbus-run-session")
                         and shutil.which("broadwayd"), "requires GTK 3 Broadway and D-Bus")
    def test_first_run_and_restoring_steam(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("home", "runtime"):
                (root / name).mkdir(mode=0o700)
            (root / "setup.py").write_text(BACKEND)
            (root / "state.json").write_text(json.dumps(dict(
                installation="missing", mode="steam", prepared=False,
                can_switch=False, setup_complete=False,
            )))
            (root / "bus.conf").write_text(BUS)
            env = dict(os.environ, HOME=str(root / "home"), XDG_RUNTIME_DIR=str(root / "runtime"),
                       XDG_CONFIG_HOME=str(root / "config"), XDG_DATA_HOME=str(root / "data"),
                       XDG_CACHE_HOME=str(root / "cache"), XDG_STATE_HOME=str(root / "state"),
                       GDK_BACKEND="broadway", BROADWAY_DISPLAY=":11", GTK_USE_PORTAL="0",
                       NO_AT_BRIDGE="1", GSETTINGS_BACKEND="memory", GTK_THEME="Adwaita", LANGUAGE="ru")
            for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "AT_SPI_BUS_ADDRESS"):
                env.pop(key, None)
            # Both GTK's display and Broadway's HTTP endpoint use private Unix sockets.
            daemon = subprocess.Popen(["broadwayd", "--unixsocket", str(root / "web.sock"), ":11"],
                                      env=env, stdout=subprocess.DEVNULL)
            try:
                for _ in range(120):
                    if (root / "web.sock").is_socket():
                        break
                    time.sleep(.01)
                subprocess.run(["dbus-run-session", "--config-file", str(root / "bus.conf"), "--",
                                sys.executable, __file__, "--private", str(root)],
                               env=env, check=True, timeout=20)
            finally:
                daemon.terminate()
                daemon.wait(timeout=3)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--private":
        private_session(Path(sys.argv[2]))
    else:
        unittest.main()
