"""Exercise controller gestures in Qt Quick with a private, offscreen window."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

RUNNER = shutil.which("qmltestrunner") or "/usr/lib/qt6/bin/qmltestrunner"


@unittest.skipUnless(Path(RUNNER).is_file(), "requires Qt Quick Test")
class ControllerGestures(unittest.TestCase):
    def test_controller_selection(self):
        self.run_case("tst_alternates.qml")

    def test_erase_never_sends_history_navigation(self):
        self.run_case("tst_erase.qml", QT_IM_MODULE="qtvirtualkeyboard",
                      QT_VIRTUALKEYBOARD_DESKTOP_DISABLE="1")

    def run_case(self, filename, **extra):
        case = Path(__file__).with_name(filename)
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "XDG_RUNTIME_DIR": directory, "QT_QPA_PLATFORM": "offscreen",
                   "QT_QUICK_BACKEND": "software", **extra}
            for name in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
                env.pop(name, None)
            result = subprocess.run([RUNNER, "-input", str(case)], env=env, capture_output=True,
                                    text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
