"""Steam Deck trackpad feedback, using the Steam keyboard preference on Linux."""
import fcntl
import json
import logging
from pathlib import Path
import re
import struct
import time
from typing import NamedTuple


class Pulse(NamedTuple):
    on_us: int
    off_us: int
    repeats: int
    gain_db: int


def selection_pulse(kind, intensity, gain):
    """Approximate Steam's tick/click choice with a short Deck pulse train."""
    if kind == 0:
        return None
    # Firmware intensity 0 means system default. Keep every train under 8 ms.
    level = intensity or 2
    if kind == 1:
        return Pulse(400 + 40 * level, 460, 2, gain)
    if kind == 2:
        return Pulse(500 + 25 * level, 630, 6, gain)
    raise ValueError("Unsupported haptic type")


# Steam keyboard presets specify effect type, intensity and gain in dB.
PRESETS = {0: None, 1: selection_pulse(1, 2, -2), 2: selection_pulse(2, 4, -5),
           3: selection_pulse(2, 3, -3)}
TICK = Pulse(300, 0, 1, -12)
HIDIOCSFEATURE_65 = 0xC0414806  # _IOC(READ|WRITE, 'H', 6, 65), Linux x86_64
SETTINGS = re.compile(r'^\s*"keyboard_haptic_(preset|custom)"\s+("(?:\\.|[^"\\])*")')
log = logging.getLogger(__name__)


def steam_keyboard_preset(userdata=None):
    if userdata is None:
        userdata = Path.home() / ".steam/steam/userdata"
    try:
        files = sorted(userdata.glob("*/config/localconfig.vdf"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files:
            options = {}
            with path.open(errors="replace") as config:
                for line in config:
                    match = SETTINGS.match(line)
                    if match:
                        options[match[1]] = json.loads(match[2])
            if "preset" not in options:
                continue
            number = int(options["preset"])
            if number in PRESETS:
                return PRESETS[number]
            if number == 10 and "custom" in options:
                custom = json.loads(options["custom"])
                kind = int(custom["eHapticType"])
                if kind in (0, 1, 2):
                    return selection_pulse(kind, max(0, min(4, int(custom["unIntensity"]))),
                                           max(-24, min(6, int(custom["ndBGain"]))))
    except (OSError, KeyError, ValueError, TypeError):
        pass
    return PRESETS[2]


def feature_report(side, effect):
    """Encode Linux hid-steam's ID_TRIGGER_HAPTIC_PULSE for a Deck hidraw fd."""
    if (side not in (0, 1, 2) or not 1 <= effect.on_us <= 1000
            or not 0 <= effect.off_us <= 1000 or not 1 <= effect.repeats <= 6
            or not -24 <= effect.gain_db <= 6):
        raise ValueError("Invalid Deck trackpad feedback")
    data = bytearray(65)
    # Report ID 0, command, payload size, physical pad, timings, count, signed gain.
    struct.pack_into("<BBBBHHHb", data, 0, 0, 0x8F, 8, side, *effect)
    return data


class TrackpadFeedback:
    def __init__(self):
        self.preset = PRESETS[2]
        self.next_tick = [0.0, 0.0]
        self.unavailable = False

    def begin(self):
        self.preset = steam_keyboard_preset()
        self.next_tick = [0.0, 0.0]
        self.unavailable = False

    def play(self, fd, clicks, hover):
        if fd is None or self.unavailable or self.preset is None:
            return
        now = time.monotonic()
        # A click takes precedence. Keep subsequent navigation ticks clear of it.
        ticks = 0
        for side in (0, 1):
            bit = 1 << side
            if clicks & bit:
                self.next_tick[side] = now + .10
            elif hover & bit and now >= self.next_tick[side]:
                ticks |= bit
                self.next_tick[side] = now + .045
        try:
            # Respect quiet custom settings for navigation as well as selection.
            navigation = TICK._replace(gain_db=max(-24, min(TICK.gain_db, self.preset.gain_db - 6)))
            for mask, effect in ((clicks, self.preset), (ticks, navigation)):
                if mask:
                    self.send(fd, mask, effect)
        except OSError as error:
            self.unavailable = True
            log.warning("Trackpad haptics unavailable for this keyboard session: %s", error)

    def send(self, fd, mask, effect):
        # 0x8F uses the historical reversed numbering on the wire.
        side = {1: 1, 2: 0, 3: 2}[mask]
        result = fcntl.ioctl(fd, HIDIOCSFEATURE_65, feature_report(side, effect), True)
        if result < 10:
            raise OSError("Deck did not accept the haptic feature report")
        return result
