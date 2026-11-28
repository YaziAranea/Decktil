#!/usr/bin/python3
"""Opt-in Decktil pilot for the SteamOS Gamescope session."""
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from gi.repository import GLib, GLibUnix

from controller import ChordEdge, DeckFrames, STEAM_UI, SteamLog, StockGate, deck_device, valid_report
from output import ACTIONS, GamescopeOutput, UnmappedKey
from overlay import available
from typed_context import TypedContext

LOG = logging.getLogger('decktil-gaming')
CHORD_REORDER_SECONDS = .20
HID_SILENCE_SECONDS = 2.0


class Pilot:
    def __init__(self, focus, send, stock_request, *, output=GamescopeOutput, plane_available=available,
                 haptic_device=None):
        self.focus, self.send = focus, send
        self.stock_request, self.output = stock_request, output
        self.plane_available = plane_available
        self.edge, self.frames, self.gate = ChordEdge(), DeckFrames(haptic_device), StockGate()
        self.ready = self.visible = self.dismissed = False
        self.show_pending = self.close_only = False
        self.xid = None
        self.stock_open = self.target = self.sink = None
        self.token = 0
        self.next_guard = self.shown_by = 0.0
        self.last_report = self.last_report_at = None
        self.displaced = None
        self.touched = self.pressed = self.typed = False
        self.words = TypedContext()
        self.pending_learning = []
        self.observed_editor = lambda now: None

    def stop(self, *, restore=False):
        # Never reopen Steam's keyboard in a different editor after focus loss.
        bring_back = (restore and self.dismissed and not self.close_only and self.target is not None
                      and self.focus.editor() == self.target)
        hide = self.show_pending or self.visible
        if self.visible:
            LOG.info('Decktil hidden (pad touch=%s, pad or trigger press=%s, input delivered=%s)',
                     self.touched, self.pressed, self.typed)
        self.gate.reset()
        self.frames.reset()
        self.words.update(None)
        self.pending_learning.clear()
        if self.sink is not None:
            self.sink.close()
        self.sink = self.target = None
        self.dismissed = self.visible = self.show_pending = self.close_only = False
        self.displaced = None
        if hide and self.ready:
            self.send({'op': 'hide'})
        if bring_back and self.stock_open is not True:
            self.stock_request('open')

    def _show(self, now, **fields):
        self.token += 1
        self.visible = False
        self.show_pending = True
        self.touched = self.pressed = self.typed = False
        self.shown_by = now + 2.0
        self.send({'op': 'show', 'token': self.token, **fields})

    def chord(self, now):
        displaced, self.displaced = self.displaced, None
        # Steam's OSK log and the HID device have independent event sources.
        # If the log wins while our panel is visible, the native keyboard has
        # already appeared in response to the same closing gesture.
        if (displaced is not None and now <= displaced[1] and self.stock_open is True
                and self.focus.editor() == displaced[0]):
            self.target, self.close_only = displaced[0], True
            self.gate.arm(self.target, now, ready=self.ready, stock_closed=True)
            self.gate.observe(True, STEAM_UI, now)
            return
        if self.target is not None:
            target = self.target
            closing = self.visible and self.stock_open is False
            self.stop()
            if closing and self.focus.editor() == target:
                # Steam receives the same physical chord and may open its keyboard.
                # Dismiss only a newly confirmed inline OSK in the original editor.
                self.target, self.close_only = target, True
                self.gate.arm(target, now, ready=self.ready, stock_closed=True)
            return
        if not self.ready:
            LOG.info('Steam+X: keyboard is warming up; Steam keeps the request')
            return
        target = self.focus.editor()
        if target is None:
            LOG.info('Steam+X: %s; Steam keeps the request', getattr(self.focus, 'reason', 'unverified editor'))
            return
        if not self.plane_available(self.xid):
            LOG.info('Steam+X: no free Gamescope overlay plane; Steam keeps the request')
            return
        inline = self.focus.inline(target)
        unknown = inline is None and self.stock_open is None
        if self.stock_open is True or inline is True or (unknown and not self.focus.clear_to_wait(target)):
            LOG.info('Steam+X: original keyboard state unconfirmed; Steam keeps the request')
            return
        self.target = target
        self.frames.reset()
        self.gate.arm(target, now, ready=self.ready, stock_closed=True)

    def report(self, packet, now):
        if not valid_report(packet):
            return
        previous = self.last_report
        self.last_report, self.last_report_at = packet, now
        if self.edge.feed(packet):
            self.chord(now)
            return
        if self.visible:
            values = self.frames.feed(packet, now)
            if values is not None:
                self.touched |= bool(values[4])
                self.pressed |= bool(values[5] & 3)
                self.send({'op': 'frame', 'values': values})
            # Steam's keyboard also uses R1 for Space. Only a fresh standalone
            # press in a verified field belongs to the visible game keyboard.
            if (previous is not None and packet[8] & 0x04 and not previous[8] & 0x04
                    and not packet[9] & 0x20 and self.prediction_field(now) is not None):
                self.event({'op': 'text', 'text': ' ', 'token': self.token}, now)

    def device_lost(self):
        self.last_report = self.last_report_at = None
        self.edge.reset()
        self.stop(restore=not self.visible)

    def stock(self, events, now):
        if events is None:
            self.stock_open = None
            self.displaced = None
            if self.target is not None:
                self.stop()
            return
        for active, appid in events:
            previously_closed = self.stock_open is False
            self.displaced = None
            self.stock_open = active
            if self.gate.stage is not None:
                self.gate.observe(active, appid, now)
                if self.gate.stage is None:
                    self.stop()
            elif active and self.target is not None:
                # An independent Steam API request always wins.
                previous = self.target if self.visible and previously_closed and appid == STEAM_UI else None
                self.stop()
                if previous is not None:
                    self.displaced = previous, now + CHORD_REORDER_SECONDS

    def tick(self, now):
        if self.target is None:
            return
        if self.gate.stage:
            stage = self.gate.stage
            current = self.focus.editor()
            inline = self.focus.inline(self.target) if self.gate.stage in ('check_inline', 'confirm_closed') else None
            command = self.gate.advance(current, now, ready=self.ready, inline=inline)
            if command == 'dismiss-stock':
                if not self.close_only and not self.plane_available(self.xid):
                    self.stop()
                    return
                if not self.close_only:
                    try:
                        self.sink = self.output()
                    except (OSError, RuntimeError) as error:
                        LOG.warning('Gamescope input unavailable (%s)', type(error).__name__)
                        self.stop()
                        return
                    if self.focus.editor() != self.target or self.focus.inline(self.target) is not True:
                        self.stop()
                        return
                if self.stock_request('close'):
                    self.dismissed = True
                else:
                    self.stop()
            elif command == 'show':
                if self.close_only:
                    self.stop()
                elif self.plane_available(self.xid):
                    self._show(now)
                else:
                    self.stop(restore=True)
            elif self.gate.stage is None:
                LOG.info('No confirmed Steam keyboard handoff (%s)', stage)
                self.stop(restore=not self.close_only)
        elif not self.visible and now >= self.shown_by:
            self.stop(restore=True)
        elif now >= self.next_guard:
            self.next_guard = now + .12
            if self.focus.editor() != self.target:
                self.stop()
            elif not self.plane_available(self.xid):
                self.stop(restore=True)
            elif getattr(self.target, "search", False):
                # This periodic guard just revalidated the specific accessible
                # search entry. Reuse it without another AT-SPI call per key.
                self.confirm_learning(self.target, now)

    def presentation_target(self):
        return self.focus.editor()

    def prediction_field(self, now):
        # LibraryFocus already bound this panel to one accessible search entry.
        if getattr(self.target, 'search', False):
            return self.target
        field = self.observed_editor(now)
        if field is None or field[0] != self.target:
            return None
        key = field[1]
        if (isinstance(key, tuple) and len(key) >= 3
                and (key[0] == 'atspi' or key[2] in ('text', 'textarea', 'search', 'contenteditable'))):
            return field
        return None

    def update_words(self, field, *, delivered_at=None, choice="", generation="", **edit):
        changed_field = field != self.words.field
        inserted, action = edit.get("inserted", ""), edit.get("action")
        previous = self.words.text[-1:] if not changed_field else ""
        explicit = delivered_at is not None and (choice or len(inserted) == 1 or action in ("enter", "backspace"))
        if changed_field or (edit and not explicit):
            self.pending_learning.clear()
            if self.visible and self.words.field is not None:
                self.send({"op": "resetLearning", "token": self.token})
        if explicit and self.visible and field is not None and (generation or inserted == " "):
            # The panel can deliver physical keystrokes before the foreground
            # field watcher finishes its next observation. Confirm both sides
            # of that interval before handing an edit to the personal model.
            if len(self.pending_learning) >= 128:
                self.pending_learning.clear()
                self.send({"op": "resetLearning", "token": self.token})
            self.pending_learning.append((delivered_at, {"text": inserted, "choice": choice,
                                          "action": action or "", "previous": previous,
                                          "generation": generation}))
        if self.words.update(field, **edit) and self.visible:
            self.send({'op': 'prediction', 'token': self.token, 'revision': self.words.revision,
                       'text': self.words.text})

    def confirm_learning(self, field, sampled_at):
        if not self.visible or not self.pending_learning:
            return
        if field is None or self.words.field != field:
            if self.pending_learning[0][0] <= sampled_at:
                self.pending_learning.clear()
                self.send({"op": "resetLearning", "token": self.token})
            return
        while self.pending_learning and self.pending_learning[0][0] <= sampled_at:
            _, edit = self.pending_learning.pop(0)
            self.send({"op": "personalEdit", "token": self.token, "edit": edit})

    def event(self, message, now):
        op = message.get('op')
        if op == 'ready':
            self.xid = message.get('xid')
            self.ready = True
            LOG.info('Decktil panel ready; waiting for a fresh Guide+X')
            return
        if self.target is None or self.gate.stage is not None or message.get('token') != self.token:
            return
        if op == 'hide' and (self.show_pending or self.visible):
            self.stop()
            return
        if op == 'shown' and self.show_pending:
            if self.presentation_target() != self.target:
                self.stop()
                return
            self.visible, self.show_pending = True, False
            self.frames.begin()
            # A pad click can be the first report after the panel's shown ACK.
            # Prime from the last physical sample so a fresh click works and a
            # trigger held across the transition cannot create a phantom key.
            if self.last_report is not None:
                self.frames.feed(self.last_report, now)
            LOG.info('Decktil displayed over the original editor')
            return
        if not self.visible:
            return
        if op == 'feedback':
            self.frames.confirm(message.get('serial'), message.get('clicked'), message.get('cues'), now)
            return
        if op == 'resetPrediction':
            self.update_words(None)
            return
        started_at = time.monotonic()
        field = self.prediction_field(now)
        action = None
        value = ""
        def same_editor():
            return self.focus.editor() == self.target
        try:
            if op == 'suggestion':
                value = self.words.completion(field, message.get('revision'), message.get('prefix'), message.get('word'))
                if value is None:
                    return
                sent = self.sink.text(value, same_editor)
            elif op == 'text' and isinstance(message.get('text'), str):
                value = message['text']
                sent = self.sink.text(value, same_editor)
            elif op == 'action' and message.get('action') in ACTIONS:
                action = message['action']
                sent = self.sink.action(action, same_editor)
            else:
                return
        except UnmappedKey:
            self.update_words(None)
            return
        except (OSError, ValueError, RuntimeError):
            self.stop(restore=True)
            return
        if not sent:
            self.stop()
        else:
            self.typed = True
            delivered_at = now + (time.monotonic() - started_at)
            self.update_words(field, inserted=value, action=action, delivered_at=delivered_at,
                              choice=message["word"] if op == "suggestion" else "",
                              generation=message.get("generation", ""))


def request_stock(action):
    from chord import _steam_request
    from profile import steam_pid
    return bool(steam_pid()) and _steam_request(action)


def main(*, big_picture=False):
    from auto_keyboard import AutoKeyboard, EditorWatch
    if not os.environ.get('DISPLAY') or (not big_picture and not os.environ.get('GAMESCOPE_WAYLAND_DISPLAY')):
        sys.exit('Decktil gaming pilot requires a Gamescope session')
    from focus import LibraryFocus
    import profile as mapping  # same Steam paths and process checks as the desktop bridge
    logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')
    loop = GLib.MainLoop()
    child = None
    def send(message):
        if child is None:
            return
        data = (json.dumps(message, ensure_ascii=False) + '\n').encode()
        try:
            if os.write(child.stdin.fileno(), data) != len(data):
                loop.quit()
        except OSError:
            loop.quit()
    stream = SteamLog(mapping.ROOT / 'logs/controller_ui.txt')
    buffer = bytearray()
    def panel_event(fd, condition):
        try:
            data = os.read(fd, 65536)
            if not data:
                loop.quit()
                return False
            buffer.extend(data)
            while b'\n' in buffer:
                line, _, rest = buffer.partition(b'\n')
                buffer[:] = rest
                pilot.event(json.loads(line), time.monotonic())
            if len(buffer) > 65536:
                raise ValueError('Panel event too long')
        except (OSError, ValueError, TypeError):
            loop.quit()
            return False
        return True
    flags = GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP
    def start_panel():
        nonlocal child
        if child is None:
            cmd = [sys.executable, '-Bs', str(Path(__file__).resolve().with_name('panel.py'))]
            if big_picture:
                cmd.append('--big-picture')
            child = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
            os.set_blocking(child.stdin.fileno(), False)
            os.set_blocking(child.stdout.fileno(), False)
            GLib.io_add_watch(child.stdout.fileno(), GLib.PRIORITY_DEFAULT, flags, panel_event)

    fd = watch = None
    def haptic_device():
        return fd

    if big_picture:
        from bigpicture_focus import BigPictureFocus
        from manual import ManualPilot
        from evdev_output import EvdevOutput
        from kde_layout import KdeLayout
        pilot = ManualPilot(BigPictureFocus(), send, EvdevOutput, start_panel,
                            transient=True, layout_factory=KdeLayout, haptic_device=haptic_device)
        manual = pilot
        auto = AutoKeyboard(manual, request_stock)
    else:
        from manual import GamescopeWindow, GamingSession, ManualPilot
        pilot = GamingSession(Pilot(LibraryFocus(), send, request_stock, haptic_device=haptic_device),
                              ManualPilot(GamescopeWindow(), send, lambda owner: GamescopeOutput(),
                                          start_panel, transient=False, haptic_device=haptic_device))
        manual = pilot.manual
        auto = AutoKeyboard(manual, request_stock, pilot.claim_manual)
        pilot.recent_editor = auto.recent_editor
        start_panel()
    manual.observed_editor = auto.prediction_editor
    editor_watch = EditorWatch(type(manual.focus))
    def device_event(source, condition):
        nonlocal fd
        try:
            if condition & (GLib.IO_ERR | GLib.IO_HUP):
                raise OSError('Device disconnected')
            for _ in range(64):
                packet = os.read(source, 64)
                if not packet:
                    raise OSError('Device closed')
                now = time.monotonic()
                if valid_report(packet):
                    auto.report(packet, now)
                pilot.report(packet, now)
        except BlockingIOError:
            pass
        except OSError:
            os.close(fd)
            fd = None
            auto.a_down = None
            pilot.device_lost()
            return False
        return True
    next_device = opened_at = 0.0
    def poll():
        nonlocal fd, watch, next_device, opened_at
        now = time.monotonic()
        last_sample = pilot.last_report_at if pilot.last_report_at is not None else opened_at
        if fd is not None and now - last_sample > HID_SILENCE_SECONDS:
            LOG.warning('Controller reports stopped; releasing keyboard and reopening the read-only device')
            GLib.source_remove(watch)
            os.close(fd)
            fd = None
            auto.a_down = None
            pilot.device_lost()
        if fd is None and now >= next_device:
            next_device = now + 2
            try:
                path = deck_device()
                if path is not None:
                    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                    opened_at = now
                    watch = GLib.io_add_watch(fd, GLib.PRIORITY_DEFAULT, flags, device_event)
                    LOG.info('Built-in Steam Deck controller connected (read-only)')
            except OSError:
                pass
        pilot.stock(stream.poll(mapping.steam_pid()), now)
        auto.tick(now)
        if fd is not None and pilot.last_report_at is not None:
            observation = editor_watch.poll(now, manual.xid)
            if observation is not None:
                owner, state, sampled_at = observation
                auto.observe(owner, state, now, sampled_at=sampled_at)
                verified_field = (owner, state[1]) if state and state[0] in ("editor", "inline") else None
                manual.confirm_learning(verified_field, sampled_at)
        else:
            auto.observe(None, None, now)
        pilot.tick(now)
        active = manual if big_picture else pilot.current
        if active.words.text:
            active.update_words(active.prediction_field(now))
        return True
    GLib.timeout_add(100, poll)
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, loop.quit)
    try:
        loop.run()
    finally:
        editor_watch.close()
        pilot.stop(restore=True)
        if fd is not None:
            os.close(fd)
        if child is not None:
            child.stdin.close()
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return int(child is not None and child.returncode not in (0, -signal.SIGTERM))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--big-picture', action='store_true')
    sys.exit(main(big_picture=parser.parse_args().big_picture))
