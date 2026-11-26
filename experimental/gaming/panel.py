#!/usr/bin/python3
"""Render Decktil above Gamescope or a fullscreen Steam window in KDE.

The supervisor owns the controller, the target and output delivery. This
process only exchanges newline-delimited JSON over its inherited pipes.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--big-picture", action="store_true")
    big_picture = parser.parse_args().big_picture
    if not os.environ.get("DISPLAY") or not (big_picture or os.environ.get("GAMESCOPE_WAYLAND_DISPLAY")):
        sys.exit("An Xwayland display and the selected Steam session are required.")
    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    snapshot = Path(__file__).resolve().parents[2]
    if (snapshot / "bindings").is_dir():
        sys.path.insert(0, str(snapshot / "bindings"))
    sys.path.insert(0, str(snapshot / "src"))
    import keyboard

    keyboard.layout_root = runtime / ("decktil-big-picture" if big_picture else "decktil-gaming") / "layouts"
    keyboard.generate_layouts(gaming=True)
    os.environ.update(QT_QPA_PLATFORM="xcb", QT_IM_MODULE="qtvirtualkeyboard",
                      QT_VIRTUALKEYBOARD_LAYOUT_PATH=str(keyboard.layout_root),
                      QT_VIRTUALKEYBOARD_STYLE="Breeze", QT_VIRTUALKEYBOARD_DESKTOP_DISABLE="1")

    from PyQt6.QtCore import (QObject, QLocale, QPoint, QProcess, QRect, QSocketNotifier, QTimer, QUrl,
                              Qt, pyqtProperty, pyqtSignal, pyqtSlot)
    from PyQt6.QtGui import QGuiApplication, QIcon, QRegion, QWindow
    from PyQt6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
    from PyQt6.QtQuick import QQuickWindow

    def event(op, **fields):
        try:
            print(json.dumps(dict(op=op, **fields), ensure_ascii=True), flush=True)
        except BrokenPipeError:
            app.quit()

    class Host(QObject):
        frame = pyqtSignal("QVariantList")
        spaceProbe = pyqtSignal("QVariantList")
        shiftChanged = pyqtSignal()
        localeSelected = pyqtSignal(str)
        predictionChanged = pyqtSignal()
        _prediction = ""
        _revision = 0
        locale_pending = False
        token = None
        _shift = False
        _caps = False
        _last_shift = 0.0

        @pyqtProperty(bool, notify=shiftChanged)
        def shifted(self):
            return self._shift

        @pyqtProperty(str, notify=predictionChanged)
        def predictionText(self):
            return self._prediction

        @pyqtProperty(int, notify=predictionChanged)
        def predictionRevision(self):
            return self._revision

        def set_prediction(self, text, revision):
            if self._prediction != text or self._revision != revision:
                self._prediction, self._revision = text, revision
                self.predictionChanged.emit()

        @pyqtSlot(str, str, int)
        def chooseSuggestion(self, prefix, word, revision):
            if self.token is not None and not self.locale_pending and self._prediction and revision == self._revision:
                event("suggestion", token=self.token, prefix=prefix, word=word, revision=revision)

        @pyqtSlot()
        def resetPredictionLocale(self):
            if self.token is not None:
                self.set_prediction("", 0)
                event("resetPrediction", token=self.token)

        def reset_shift(self):
            self._shift = self._caps = False
            self._last_shift = 0.0
            self.shiftChanged.emit()

        @pyqtSlot()
        def toggleShift(self):
            if self.token is None:
                return
            now = time.monotonic()
            if self._shift and not self._caps and now - self._last_shift < .45:
                self._caps = True
            elif self._shift:
                self.reset_shift()
                return
            else:
                self._shift = True
            self._last_shift = now
            self.shiftChanged.emit()

        @pyqtProperty(str, constant=True)
        def initialLocale(self):
            return "ru_RU" if QLocale().name().startswith("ru") else "en_US"

        @pyqtSlot(str, result=bool)
        def requestLocale(self, locale):
            if not big_picture:
                return False
            if self.token is not None and not self.locale_pending and locale in ("ru_RU", "en_US"):
                self.locale_pending = True
                event("locale", locale=locale, token=self.token)
            return True

        @pyqtSlot(int, str)
        def key(self, key, text):
            if self.token is None or self.locale_pending:
                return
            actions = {Qt.Key.Key_Backspace: "backspace", Qt.Key.Key_Delete: "delete",
                       Qt.Key.Key_Return: "enter", Qt.Key.Key_Enter: "enter",
                       Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right",
                       Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down"}
            if key in actions:
                event("action", action=actions[key], token=self.token)
            elif text and len(text) <= 32 and text.isprintable():
                event("text", text=text, token=self.token)
                if self._shift and not self._caps:
                    self.reset_shift()

        @pyqtSlot()
        def dismiss(self):
            if self.token is not None:
                event("hide", token=self.token)

        @pyqtSlot(int, int, int)
        def feedback(self, serial, clicked, cues):
            if self.token is not None and serial > 0:
                event("feedback", token=self.token, serial=serial, clicked=clicked, cues=cues)

        @pyqtSlot(int)
        def spaceIntent(self, serial):
            if self.token is not None and serial > 0:
                event("spaceIntent", token=self.token, serial=serial)

    # KDE's installed Breeze style imports these settings from the
    # plasma-keyboard executable. Supply the few roles its key panels use
    # when embedding the same style outside the KWin input method.
    class BreezeSettings(QObject):
        panelFillScreenWidth = pyqtProperty(bool, lambda self: True, constant=True)
        soundEnabled = pyqtProperty(bool, lambda self: False, constant=True)
        vibrationEnabled = pyqtProperty(bool, lambda self: False, constant=True)

    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QGuiApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    QIcon.setFallbackThemeName("breeze")
    if QIcon.themeName() in ("", "hicolor"):
        QIcon.setThemeName("breeze")
    host = Host()
    style_settings = BreezeSettings()
    qmlRegisterSingletonInstance("org.kde.plasma.keyboard", 1, 0, "PlasmaKeyboardSettings", style_settings)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("decktilGamePanel", host)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("panel.qml").resolve())))
    if not engine.rootObjects():
        sys.exit("Could not load the Decktil Gaming panel.")
    window = engine.rootObjects()[0]
    if big_picture:
        window.setFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    xid = int(window.winId())  # Realize without mapping; the focused window stays intact.

    def external(enabled):
        if big_picture:
            return
        subprocess.run(["xprop", "-id", str(xid), "-f", "GAMESCOPE_EXTERNAL_OVERLAY", "32c",
                        "-set", "GAMESCOPE_EXTERNAL_OVERLAY", "1" if enabled else "0"],
                       check=True, timeout=2)

    external(False)
    owner = None

    def active_window():
        data = subprocess.check_output(["xprop", "-root", "_NET_ACTIVE_WINDOW"], timeout=.5)
        match = re.search(rb"\b0x[0-9a-fA-F]+\b", data)
        return int(match[0], 16) if match else 0

    def mask():
        h = min(window.height(), window.property("surfaceHeight"))
        if h > 0:
            window.setMask(QRegion(0, window.height() - h, window.width(), h))

    def hide():
        nonlocal owner
        old = host.token
        shown_timer.stop()
        startup_timeout.stop()
        host.token = None
        host.locale_pending = False
        host.set_prediction("", 0)
        host.reset_shift()
        host.frame.emit([0] * 10)
        window.hide()
        window.setProperty("coveringNative", False)
        if owner is not None:
            window.setTransientParent(None)
            owner.deleteLater()
            owner = None
        external(False)
        event("hidden", token=old)

    def focus_changed():
        while focus_watch.canReadLine():
            focus_watch.readLine()
        if owner is not None and host.token is not None:
            try:
                current = active_window()
            except (OSError, subprocess.SubprocessError):
                current = 0
            if current != int(owner.winId()):
                host.dismiss()
                hide()

    def command(request):
        nonlocal owner
        op = request["op"]
        if op == "quit":
            app.quit()
        elif op == "show":
            token = request["token"]
            if type(token) is not int or token <= 0:
                raise ValueError("invalid open token")
            if host.token == token and window.isVisible():
                return
            if window.isVisible():
                hide()
            host.token = token
            if big_picture:
                locale = request.get("locale")
                if locale in ("ru_RU", "en_US"):
                    host.localeSelected.emit(locale)
                target = request.get("owner")
                if type(target) is not int or not 0 < target <= 0xffffffff:
                    raise ValueError("invalid Big Picture window")
                if active_window() != target:
                    host.dismiss()
                    hide()
                    return
                owner = QWindow.fromWinId(target)
                if owner is None or owner.size().isEmpty():
                    host.dismiss()
                    hide()
                    return
                window.setTransientParent(owner)
                window.setGeometry(QRect(owner.mapToGlobal(QPoint(0, 0)), owner.size()))
            window.setProperty("coveringNative", request.get("cover") is True)
            mask()
            external(True)  # Set before mapping, so the editor keeps focus.
            window.show()
            if big_picture:
                window.raise_()
            shown_timer.start()
            startup_timeout.start()
        elif op == "hide":
            hide()
        elif op == "cover" and host.token is not None and request.get("token") == host.token:
            if type(request.get("active")) is bool:
                window.setProperty("coveringNative", request["active"])
        elif op == "prediction" and host.token is not None and request.get("token") == host.token:
            text, revision = request["text"], request["revision"]
            if isinstance(text, str) and len(text) <= 256 and type(revision) is int:
                host.set_prediction(text, revision)
        elif op == "locale" and big_picture and host.token is not None and request.get("token") == host.token:
            host.locale_pending = False
            locale = request.get("locale")
            if locale in ("ru_RU", "en_US"):
                host.localeSelected.emit(locale)
        elif op in ("frame", "probeSpace") and host.token is not None:
            if op == "probeSpace" and (request.get("token") != host.token or not window.property("coveringNative")):
                return
            values = request["values"]
            if (not isinstance(values, list) or len(values) != 10
                    or not all(type(n) is int and -2147483648 <= n <= 2147483647 for n in values)):
                raise ValueError("invalid controller frame")
            if op == "probeSpace":
                host.spaceProbe.emit(values)
            else:
                host.frame.emit(values)

    pending = bytearray()
    os.set_blocking(0, False)
    notifier = QSocketNotifier(0, QSocketNotifier.Type.Read)

    def read():
        try:
            chunk = os.read(0, 8192)
            if not chunk:
                app.quit()
                return
            pending.extend(chunk)
            while (end := pending.find(b"\n")) != -1:
                line = bytes(pending[:end])
                del pending[:end + 1]
                if len(line) > 4096:
                    raise ValueError("oversized command")
                command(json.loads(line))
            if len(pending) > 4096:
                raise ValueError("oversized command")
        except (KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError):
            app.exit(2)

    notifier.activated.connect(read)
    # The xcb window stays unmapped until asked. Some Qt styles resolve the
    # screen-dependent keyboard dimensions only after their first mapping.
    def shown():
        if window.property("selector") is not None and window.property("panelHeight") > 0:
            shown_timer.stop()
            startup_timeout.stop()
            mask()
            event("shown", token=host.token)

    shown_timer = QTimer(interval=25)
    shown_timer.timeout.connect(shown)
    startup_timeout = QTimer(interval=12000, singleShot=True)
    startup_timeout.timeout.connect(lambda: app.exit(3))
    window.surfaceHeightChanged.connect(mask)
    window.widthChanged.connect(mask)
    window.heightChanged.connect(mask)
    if big_picture:
        # KWin clears _NET_ACTIVE_WINDOW when focus leaves Xwayland for a
        # native Wayland app. Observe that edge even if the controller is idle.
        focus_watch = QProcess(app)
        focus_watch.readyReadStandardOutput.connect(focus_changed)
        focus_watch.finished.connect(lambda *_: app.exit(3))
        focus_watch.start("xprop", ["-spy", "-root", "_NET_ACTIVE_WINDOW"])
        if not focus_watch.waitForStarted(1000):
            sys.exit("Could not watch the active Big Picture window.")
    event("ready", xid=xid)
    result = app.exec()
    if big_picture:
        focus_watch.kill()
        focus_watch.waitForFinished(1000)
    return result


if __name__ == "__main__":
    sys.exit(main())
