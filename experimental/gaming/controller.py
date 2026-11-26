"""Read-only Deck input and conservative handoff from Steam's built-in keyboard."""
import os
from pathlib import Path
import re
import struct
import sys
import time

# Share the desktop trigger hysteresis, visual pressure scale and Deck detection.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from bridge import ERASE_BUTTON, deck_device, display_force, trigger_mask
from haptics import TrackpadFeedback


STEAM_UI = 769
OSK_EVENT = re.compile(rb"\] Set OSK active ([01]) and appid (-?[0-9]{1,12})\r?$")


def valid_report(report):
    return len(report) == 64 and report[:4] == b"\x01\x00\x09\x40"


class ChordEdge:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x_down = None

    def feed(self, report):
        if not valid_report(report):
            return False
        previous = self.x_down
        self.x_down = bool(report[8] & 0x40)
        return previous is False and self.x_down and bool(report[9] & 0x20)


class PadPressure:
    """Recognize a light press without turning a passing thumb into a click."""
    # Match the desktop bridge's light haptic detent and its release threshold.
    PRESS = 950
    RELEASE = 350
    STABLE_REPORTS = 3  # About 8 ms from first to third on the built-in Deck.
    MAX_TRAVEL = 1600  # About 2.4% of the trackpad's coordinate range.

    def __init__(self):
        self.missing = False
        self.physical_down = False
        self.release()

    def release(self):
        self.down = self.dragged = False
        self.samples = 0
        self.origin = (0, 0)

    def feed(self, touched, force, position, baseline, physical_down):
        physical_release = self.physical_down and not physical_down
        self.physical_down = physical_down
        if not touched:
            # One missing contact bit is common during a firm press.
            if self.missing or (physical_release and force <= self.RELEASE):
                self.release()
            self.samples = 0
            self.missing = True
            return self.down
        self.missing = False
        if force <= self.RELEASE:
            self.release()
        elif physical_down and force >= self.PRESS:
            # A switch or trigger can complete the same press before its
            # pressure samples settle. Keep the cycle latched until relaxation.
            self.down = True
        elif not self.down and not self.dragged:
            if force < self.PRESS:
                self.samples = 0
            elif baseline:
                self.down = True  # Never select from pressure held before show.
            else:
                if not self.samples:
                    self.origin = position
                if (abs(position[0] - self.origin[0]) > self.MAX_TRAVEL
                        or abs(position[1] - self.origin[1]) > self.MAX_TRAVEL):
                    self.dragged = True  # Wait for relaxation before rearming.
                else:
                    self.samples += 1
                    self.down = self.samples >= self.STABLE_REPORTS
        return self.down


class DeckFrames:
    """Make DeckSelector frames only while the supervisor owns the panel."""
    def __init__(self, haptic_device=None):
        self.haptic_device = haptic_device
        self.feedback = TrackpadFeedback()
        self.serial = 0
        self.reset()

    def reset(self):
        self.down = None
        self.triggers = 0
        self.pressure = (PadPressure(), PadPressure())
        self.last = None
        self.sent = 0.0
        self.recent = {}

    def begin(self):
        if self.haptic_device is not None:
            self.feedback.begin()

    def confirm(self, serial, clicked, cues, now):
        if not all(type(n) is int for n in (serial, clicked, cues)) or not (0 <= clicked <= 3 and 0 <= cues <= 15):
            return
        frame = self.recent.pop(serial, None)
        if frame is None:
            return
        stamp, touches, pressed, down = frame
        if now - stamp > .3:
            return
        click = clicked & pressed
        hover = cues & touches & ~down
        if click or hover:
            # Confirm the selected key immediately with Steam's configured pulse.
            # Pressure still drives the visual ring; it never changes this click.
            self.feedback.play(self.haptic_device(), click, hover)

    def feed(self, report, now=None):
        if not valid_report(report):
            return None
        if report[9] & 0x20:
            needs_release = self.last is not None and bool(self.last[4] or self.last[5])
            self.reset()
            if needs_release:
                self.serial = self.serial % 0x3fffffff + 1
                return (0,) * 9 + (self.serial,)
            return None
        if now is None:
            now = time.monotonic()
        touches = (report[10] >> 3) & 3
        clicks = (report[10] >> 1) & 3
        self.triggers = trigger_mask(report, self.triggers)
        forces = struct.unpack_from("<HH", report, 56)
        position = struct.unpack_from("<hhhh", report, 16)
        # Track the physical switch through a momentary loss of touch. Only a
        # new down edge on a touched pad may select a key.
        down = self.triggers | clicks
        for side, pad in enumerate(self.pressure):
            bit = 1 << side
            point = position[side * 2:side * 2 + 2]
            if pad.feed(touches & bit, forces[side], point, self.down is None, down & bit):
                down |= bit
        # The open panel uses the same X / Backspace gesture as desktop.
        # A held pad needs a contact; the physical X button works independently.
        if report[8] & 0x40:
            down |= ERASE_BUTTON
        held = down & (touches | ERASE_BUTTON)
        pressed = held & ~self.down if self.down is not None else 0
        self.down = down
        scaled = tuple(display_force(value) if touches & (1 << i) else 0
                       for i, value in enumerate(forces))
        state = (*(position if touches else (0, 0, 0, 0)), touches, held, *scaled)
        if self.last is not None and not pressed and state[4:6] == self.last[4:6]:
            if state == self.last and (not (touches or held) or now - self.sent < .1):
                return None
            if state != self.last and now - self.sent < 1 / 60:
                return None
        self.last, self.sent = state, now
        self.serial = self.serial % 0x3fffffff + 1
        if self.haptic_device is not None and touches:
            if len(self.recent) >= 48:
                del self.recent[next(iter(self.recent))]
            self.recent[self.serial] = (now, touches, pressed, down)
        return (*state[:5], pressed, *state[5:], self.serial)


class SteamLog:
    """Read fresh OSK edges only; None signals an unverifiable interval."""
    def __init__(self, path):
        self.path = Path(path)
        self.identity = None
        self.offset = 0
        self.partial = b""

    def poll(self, steam_pid):
        try:
            with self.path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                identity = (steam_pid, info.st_dev, info.st_ino)
                if (not steam_pid or self.identity != identity or info.st_size < self.offset
                        or info.st_size - self.offset > 256 * 1024):
                    self.identity, self.offset, self.partial = identity, info.st_size, b""
                    return None
                stream.seek(self.offset)
                data = stream.read(info.st_size - self.offset)
                self.offset = stream.tell()
        except OSError:
            self.identity, self.partial = None, b""
            return None
        lines = (self.partial + data).split(b"\n")
        self.partial = lines.pop()
        if len(self.partial) > 8192:
            self.identity, self.partial = None, b""
            return None
        events = []
        for line in lines:
            match = OSK_EVENT.search(line)
            if match:
                events.append((match[1] == b"1", int(match[2])))
        return events


class StockGate:
    """Hand off only a confirmed inline keyboard on the same Library editor.

    The caller supplies a stable window + editor identity, or None when
    focus, editability, main-library ownership or absence of games is unknown.
    `inline` is independent UI evidence for that exact editor. A matching log
    line, generic Steam window title or visible keyboard keys cannot establish
    this: Steamworks callback dialogs share the keyboard and return a result.
    """
    def __init__(self):
        self.reset()

    def reset(self):
        self.target = self.stage = None
        self.deadline = 0.0

    def arm(self, target, now, *, ready, stock_closed):
        self.reset()
        if target is not None and ready and stock_closed is True:
            self.target, self.stage, self.deadline = target, "await_open", now + 1.25

    def observe(self, active, appid, now):
        if self.stage is None:
            return
        if now > self.deadline or appid != STEAM_UI:
            self.reset()
        elif self.stage == "await_open" and active:
            self.stage = "check_inline"
        elif self.stage == "await_close" and not active:
            self.stage = "confirm_closed"
        else:
            # An intervening request, a spontaneous close or ambiguous events
            # must leave Steam in charge.
            self.reset()

    def advance(self, target, now, *, ready, inline):
        if self.stage is None:
            return None
        if not ready or target != self.target or now > self.deadline:
            self.reset()
        elif self.stage == "check_inline" and inline is True:
            self.stage, self.deadline = "await_close", now + 1.25
            return "dismiss-stock"
        elif self.stage == "confirm_closed" and inline is False:
            self.reset()
            return "show"
        return None
