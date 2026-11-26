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
    service = "enabled" if args[1] == "plasma" else "disabled"
    state.update(mode=args[1], setup_complete=True, big_picture=service, gaming_mode=service,
                 switches=state.get("switches", 0) + 1)
elif len(args) == 2 and args[0] == 'chrome':
    state.update(chrome_accessibility=args[1], chrome_managed=args[1] == 'on')
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

    def has_text(text):
        return any(isinstance(child, Gtk.Label) and child.get_text() == text for child in children(window))

    def check_summary(mode, provider):
        assert has_text(window.t("current_" + mode))
        keyboards = manager.session_keyboards(window.state)
        assert set(keyboards.values()) == {provider}
        for key in keyboards:
            assert has_text(window.t(key)) and has_text(window.t("provider_" + provider))

    finish()
    check_summary("steam", "steam")
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
    check_summary("plasma", "plasma")
    click("reapply")
    finish()
    assert window.state["switches"] == 2
    check_summary("plasma", "plasma")
    click("steam")
    finish()
    check_summary("steam", "steam")
    click("plasma")
    finish()
    assert window.state["mode"] == "plasma"
    assert manager.preferred_language() == 1
    assert has_text(window.t("ready_plasma"))
    click("chrome")
    assert has_text(window.t("chrome_off")) and has_text(window.t("chrome_restart"))
    click("chrome_enable")
    finish()
    assert window.page == "chrome" and has_text(window.t("chrome_on"))
    click("chrome_restore")
    finish()
    assert has_text(window.t("chrome_off"))
    click("back")
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
    page().theme_buttons["lagoon"].clicked()
    assert appearance.load()["preset"] == "default"
    page().apply.clicked()
    assert appearance.load()["preset"] == "lagoon"
    picker = page().pickers["keyStart"]
    picker.set_rgba(rgba("#7D31B7"))
    picker.emit("color-set")
    page().direction.set_active_id("horizontal")
    assert page().theme_buttons["custom"].get_active()
    window.language.set_active(0)
    assert page().draft["keyStart"].lower() == "#7d31b7"
    page().apply.clicked()
    page().theme_buttons["default"].clicked()
    page().apply.clicked()
    click("back")
    click("appearance")
    assert page().theme_buttons["default"].get_active()
    page().theme_buttons["custom"].clicked()
    assert page().draft["keyStart"].lower() == "#7d31b7" and page().draft["direction"] == "horizontal"
    assert page().details.get_expanded()
    page().contrast.clicked()
    assert page().draft["text"] == appearance.readable_ink(page().draft)
    page().theme_buttons["porcelain"].clicked()
    assert not page().details.get_expanded()
    assert page().draft["text"] == appearance.PRESETS["porcelain"]["text"]
    click("back")
    assert appearance.load()["preset"] == "default"
    fixture = root / "state.json"
    state = json.loads(fixture.read_text())
    # Previously configured Chrome remains under the user’s control.
    state["chrome_accessibility"] = "on"
    fixture.write_text(json.dumps(state))
    click("refresh")
    finish()
    click("chrome")
    assert has_text(window.t("chrome_own")) and not has_text(window.t("chrome_restore"))
    click("back")
    state.update(installation="manual", big_picture="missing", gaming_mode="disabled",
                 chrome_accessibility="unavailable")
    fixture.write_text(json.dumps(state))
    click("refresh")
    finish()
    assert not has_text(window.t("chrome"))
    assert window.state["installation"] == "manual" and has_text(window.t("current_mixed"))
    click("plasma")
    finish()
    check_summary("plasma", "plasma")
    # A standalone gaming pilot still needs a working Restore button when KDE is already stock.
    state.update(installation="missing", prepared=False, setup_complete=False, can_switch=False,
                 mode="steam", big_picture="missing", gaming_mode="enabled")
    fixture.write_text(json.dumps(state))
    click("refresh")
    finish()
    assert has_text(window.t("current_mixed"))
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
                can_switch=False, setup_complete=False, big_picture="missing", gaming_mode="missing",
                chrome_accessibility="off", chrome_managed=False,
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
