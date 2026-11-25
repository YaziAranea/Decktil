"""Run the prediction regression cases in Qt's JavaScript engine."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


QML = shutil.which("qml6") or shutil.which("qml") or "/usr/lib/qt6/bin/qml"


class Predictions(unittest.TestCase):
    @unittest.skipUnless(Path(QML).is_file(), "requires the Qt 6 QML runner")
    def test_native_prediction_rules(self):
        self.run_case("prediction.qml")

    @unittest.skipUnless(Path(QML).is_file(), "requires the Qt 6 QML runner")
    def test_native_typo_corrections(self):
        self.run_case("typos.qml")

    def run_case(self, name):
        with tempfile.TemporaryDirectory() as private:
            env = dict(os.environ, XDG_RUNTIME_DIR=private, QT_QPA_PLATFORM="offscreen",
                       QT_QUICK_BACKEND="software", QT_FORCE_STDERR_LOGGING="1")
            for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
                env.pop(key, None)
            result = subprocess.run([QML, str(Path(__file__).with_name(name))],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
