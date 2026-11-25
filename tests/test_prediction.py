"""Run the prediction regression cases in Qt's JavaScript engine."""
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


QML = shutil.which("qml6") or shutil.which("qml") or "/usr/lib/qt6/bin/qml"


class Predictions(unittest.TestCase):
    @unittest.skipUnless(Path(QML).is_file(), "requires the Qt 6 QML runner")
    def test_native_prediction_rules(self):
        self.run_case("prediction.qml")

    @unittest.skipUnless(Path(QML).is_file(), "requires the Qt 6 QML runner")
    def test_native_typo_corrections(self):
        self.run_case("typos.qml")

    @unittest.skipUnless(Path(QML).is_file(), "requires the Qt 6 QML runner")
    def test_russian_inflections_with_packaged_models(self):
        source = Path(__file__).resolve().parents[1] / "src"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, XDG_RUNTIME_DIR=directory):
                keyboard = runpy.run_path(str(source / "keyboard.py"))
            for name in ("DeckPrediction.js", "DeckGrammar.js"):
                shutil.copyfile(source / name, root / name)
            keyboard["write_completions"](root / "DeckWords.js", source / "words/ru.txt.gz", root / "DeckSpelling.js")
            keyboard["write_predictions"](root / "DeckNext.js", source / "words/ru-next.json.gz")
            keyboard["write_morphology"](root / "DeckMorphData.js", source / "words/ru-morph.json.gz")
            case = root / "grammar.qml"
            shutil.copyfile(Path(__file__).with_name(case.name), case)
            self.run_case(case)

    def run_case(self, name):
        with tempfile.TemporaryDirectory() as private:
            env = dict(os.environ, XDG_RUNTIME_DIR=private, QT_QPA_PLATFORM="offscreen",
                       QT_QUICK_BACKEND="software", QT_FORCE_STDERR_LOGGING="1")
            for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
                env.pop(key, None)
            result = subprocess.run([QML, str(Path(__file__).parent / name)],
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
