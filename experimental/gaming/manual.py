"""Handle an explicit Steam+X when Steam leaves the fullscreen window in control."""
import logging

from controller import STEAM_UI, valid_report
from session import Pilot

LOG = logging.getLogger('decktil-gaming')


class ManualPilot(Pilot):
    """Observe the release before showing: Steam's SHOW_KEYBOARD also fires on release."""

    def __init__(self, focus, send, output, warm, *, transient, layout_factory=None, haptic_device=None):
        super().__init__(focus, send, None, output=output, haptic_device=haptic_device)
        self.warm = warm
        self.transient = transient
        self.waiting = False
        self.release_at = None
        self.deadline = 0.0
        self.native_grace_until = 0.0
        self.layout_factory = layout_factory
        self.layout = None
        self.panel_locale = None
        self.user_closed_at = None
        self.user_closed_owner = None
        self.native_handoff = False
        self.cover_words = None
        self.handoff_deadline = 0.0
        self.probe_space = False
        self.space_probe = None
        self.select_space = None

    def stop(self, *, restore=False):
        self.cover_words = None
        self.native_handoff = False
        self.probe_space = False
        self.space_probe = None
        self.waiting = False
        self.release_at = None
        if self.layout is not None:
            self.layout.close()
            self.layout = None
        self.panel_locale = None
        super().stop()

    def chord(self, now):
        if self.target is not None:
            self.user_closed_at = now
            self.user_closed_owner = self.target
            self.stop()
            return
        target = self.focus.target()
        if target is None or self.stock_open is True or now < self.native_grace_until:
            LOG.info('Steam+X kept by Steam (%s)', self.focus.reason if target is None else 'native keyboard')
            return
        self.target, self.waiting = target, True
        self.deadline = now + 6
        self.warm()
        LOG.info('Steam+X received in fullscreen Steam')

    def report(self, packet, now):
        if not valid_report(packet):
            return
        if self.native_handoff:
            # Keep the panel mapped and honor its closing chord while Steam
            # dismisses its inline UI. No keystrokes go to that UI.
            self.last_report, self.last_report_at = packet, now
            if self.edge.feed(packet):
                self.chord(now)
            elif self.probe_space:
                values = self.frames.feed(packet, now)
                if values is not None and values[5] & 1:
                    self.space_probe = (values[9], now)
                    self.send({'op': 'probeSpace', 'token': self.token, 'values': values})
            return
        super().report(packet, now)
        if (self.waiting and self.release_at is None
                and not (packet[8] & 0x40 or packet[9] & 0x20)):
            self.release_at = now

    def stock(self, events, now):
        if events is None:
            self.stock_open = None
            return
        for active, appid in events:
            # Native requests from games and desktop apps may never emit a close
            # after their process exits. Only Steam UI owns the window we target.
            self.stock_open = active and appid == STEAM_UI
            self.native_grace_until = now + .5 if active else 0.0
            if active and self.target is not None:
                if (appid == STEAM_UI and (self.visible or self.native_handoff)
                        and self.focus.target(allow_inline=True) == self.target
                        and not self.focus.native_visible()):
                    if not self.native_handoff:
                        self.pause_for_native(now)
                else:
                    self.stop()

    def event(self, message, now):
        op = message.get('op')
        if op == 'resetPrediction' and message.get('token') == self.token:
            self.cover_words = None
        if self.visible and message.get('token') == self.token and self.select_space:
            if op == 'spaceIntent':
                probe = self.space_probe
                if (self.probe_space and probe and message.get('serial') == probe[0]
                        and now - probe[1] <= .35):
                    self.space_probe = None
                    self.select_space(now, probe_serial=probe[0])
                return
            if op == 'text' and message.get('text') == ' ' and self.select_space(now):
                return
        if self.native_handoff and message.get('op') in ('text', 'feedback', 'action', 'locale', 'suggestion'):
            if message['op'] == 'locale' and self.layout and message.get('token') == self.token:
                self._sync_locale(acknowledge=True)
            return
        if (message.get('op') == 'hide' and self.visible
                and message.get('token') == self.token):
            self.user_closed_at = now
            self.user_closed_owner = self.target
        if message.get('op') == 'ready':
            self.focus.panel_xid = message.get('xid')
        if message.get('op') == 'locale' and self.layout is not None:
            if not self.visible or message.get('token') != self.token:
                return
            if not self.focus.matches(self.target):
                self.stop()
                return
            requested = message.get('locale')
            if requested in ('ru_RU', 'en_US'):
                self.layout.select(requested)
            self._sync_locale(acknowledge=True)
            return
        super().event(message, now)

    def pause_for_native(self, now):
        if self.words.text:
            self.cover_words = (self.words.field, self.words.text)
        self.update_words(None)
        self.native_handoff = True
        self.handoff_deadline = now + .65
        self.frames.reset()
        self.send({'op': 'frame', 'values': [0] * 10})
        self.send({'op': 'cover', 'token': self.token, 'active': True})

    def resume_editor(self, now):
        self.native_handoff = False
        # The animation can temporarily give focus to a search-tab button.
        # Redisplay our typed suffix only after identifying the exact same field.
        field = self.prediction_field(now)
        if self.cover_words is not None and field == self.cover_words[0]:
            self.update_words(field, inserted=self.cover_words[1])
        self.cover_words = None
        self.probe_space = False
        self.space_probe = None
        self.send({'op': 'cover', 'token': self.token, 'active': False})
        self.frames.reset()
        if self.last_report is not None:
            self.frames.feed(self.last_report, now)

    def deliver_space(self, now):
        super().event({'op': 'text', 'token': self.token, 'text': ' '}, now)

    def _sync_locale(self, *, acknowledge=False):
        current = self.layout.locale()
        if acknowledge or current != self.panel_locale:
            self.panel_locale = current
            self.send({'op': 'locale', 'token': self.token, 'locale': current})

    def presentation_target(self):
        if self.native_handoff:
            return self.focus.target(allow_inline=True)
        return self.focus.editor()

    def open_for(self, owner, now, *, covering_inline=False):
        """Map only over the original window. Block input during an inline handoff."""
        self.waiting = False
        self.target = owner
        self.native_handoff = covering_inline
        if covering_inline:
            self.handoff_deadline = now + 2.5
        if not self.ready or self.presentation_target() != owner or self.focus.native_visible():
            self.stop()
            return False
        try:
            if self.layout_factory:
                self.layout = self.layout_factory(lambda: self.focus.matches(owner))
                self.panel_locale = self.layout.locale()
            self.frames.reset()
            fields = {'owner': owner} if self.transient else {}
            if self.panel_locale:
                fields['locale'] = self.panel_locale
            if covering_inline:
                # Map the non-interactive cover while the compositor registers
                # the output device. Dismissal requires a successful registration
                # and a subsequent displayed acknowledgment.
                self._show(now, cover=True, **fields)
            self.sink = self.output(owner)
            if not covering_inline:
                self._show(now, **fields)
        except (OSError, RuntimeError, ValueError) as error:
            LOG.warning('Fullscreen keyboard output unavailable: %s', error)
            self.stop()
            return False
        return True

    def tick(self, now):
        if self.target is None:
            return
        if self.show_pending and now >= self.shown_by:
            self.stop()
            return
        if self.native_handoff:
            if now >= self.handoff_deadline:
                self.stop()
            elif now >= self.next_guard:
                self.next_guard = now + .15
                if self.focus.target(allow_inline=True) != self.target:
                    self.stop()
            return
        if self.waiting:
            if now > self.deadline:
                self.stop()
                return
            if not self.ready or self.release_at is None or now - self.release_at < .25:
                return
            self.open_for(self.target, now)
        elif now >= self.next_guard:
            self.next_guard = now + .15
            if not self.focus.matches(self.target):
                # Steam can raise its inline UI between the worker's editor
                # samples. Freeze this window's input until the worker catches
                # up; independent games, dialogs and focus loss still close it.
                if (self.visible and self.focus.target(allow_inline=True) == self.target
                        and not self.focus.native_visible()):
                    self.pause_for_native(now)
                else:
                    self.stop()
            elif self.layout is not None and self.visible:
                self._sync_locale()


class GamescopeWindow:
    """Use Gamescope's actual input and graphics focus and its free overlay slot."""
    panel_xid = None
    reason = "no-idle-steam-window-or-overlay"

    def __init__(self):
        from focus import AccessibleBlockers
        self.accessible = AccessibleBlockers()

    def target(self, *, allow_inline=False):
        from focus import ERRORS, _window
        from overlay import available
        try:
            window = _window()
            # During cold start remember the chord. After the panel's ready
            # event matches() checks its actual overlay slot before mapping.
            if window and (self.panel_xid is None or available(self.panel_xid)):
                blocker = self.accessible.check(window[1])
                if not blocker or (allow_inline and blocker == 'native-keyboard'):
                    return window[0]
        except ERRORS:
            pass
        return None

    editor = target

    def matches(self, xid):
        return self.target() == xid

    def native_visible(self):
        from chord import _steam_keyboard
        return _steam_keyboard() is not None


class GamingSession:
    """Prefer the library's native handoff. Handle a silent manual call in the Steam UI."""
    def __init__(self, library, manual):
        self.library, self.manual = library, manual
        self.current = library
        self.log_live = False
        self.recent_editor = lambda now: None
        manual.edge = library.edge

    @property
    def last_report_at(self):
        return self.current.last_report_at

    def _select(self, pilot):
        pilot.token = self.current.token
        self.current = pilot

    def claim_manual(self):
        if self.current is not self.manual:
            if self.current.target is not None:
                return False
            self.manual.last_report = self.current.last_report
            self.manual.last_report_at = self.current.last_report_at
            self._select(self.manual)
        return True

    def report(self, packet, now):
        if not valid_report(packet):
            return
        edge = self.current.edge
        pressing = edge.x_down is False and packet[8] & 0x40 and packet[9] & 0x20
        if pressing and self.current.target is None:
            displaced = self.library.displaced
            native_handoff = displaced and now <= displaced[1]
            use_library = native_handoff or (not self.recent_editor(now) and self.log_live
                                             and self.library.ready and self.library.focus.editor() is not None)
            self._select(self.library if use_library else self.manual)
        self.current.report(packet, now)

    def event(self, message, now):
        if message.get('op') == 'ready':
            self.library.event(message, now)
            self.manual.event(message, now)
        else:
            self.current.event(message, now)

    def stock(self, events, now):
        self.log_live = events is not None
        self.library.stock(events, now)
        self.manual.stock(events, now)

    def tick(self, now):
        # A verified editor can still be on a Steam build which never emits
        # an OSK-open event. Do not take over an observed but ambiguous dialog.
        lib = self.library
        if (self.current is lib and lib.gate.stage == 'await_open' and now > lib.gate.deadline
                and lib.stock_open is not True and lib.focus.editor() == lib.target
                and lib.focus.inline(lib.target) is not True):
            sample = lib.last_report
            sampled_at = lib.last_report_at
            lib.stop()
            if sample is not None:
                self._select(self.manual)
                self.manual.last_report, self.manual.last_report_at = sample, sampled_at
                self.manual.chord(now)
                if self.manual.waiting and not (sample[8] & 0x40 or sample[9] & 0x20):
                    self.manual.release_at = now
        self.current.tick(now)

    def stop(self, *, restore=False):
        self.current.stop(restore=restore)

    def device_lost(self):
        self.current.device_lost()
