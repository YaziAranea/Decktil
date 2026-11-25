"""Exercise native QML hold gestures with a private, offscreen window."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

RUNNER = shutil.which("qmltestrunner") or "/usr/lib/qt6/bin/qmltestrunner"


class AlternateGestures(unittest.TestCase):
    @unittest.skipUnless(Path(RUNNER).is_file(), "requires Qt Quick Test")
    def test_controller_selection(self):
        case = Path(__file__).with_name("tst_alternates.qml")
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "XDG_RUNTIME_DIR": directory, "QT_QPA_PLATFORM": "offscreen",
                   "QT_QUICK_BACKEND": "software"}
            result = subprocess.run([RUNNER, "-input", str(case)], env=env, capture_output=True,
                                    text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
