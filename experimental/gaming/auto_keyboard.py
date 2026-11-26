"""Follow editable fields in the foreground Steam shell without grabbing its controller."""
from concurrent.futures import ThreadPoolExecutor
import logging
import struct

from search_space import recover, search_identity

LOG = logging.getLogger("decktil-gaming")
MAX_OBSERVATION_AGE = 1.5


class EditorWatch:
    """Keep DOM/accessibility queries off the HID loop; never accumulate pending polls."""

    def __init__(self, focus_type):
        self.focus_type = focus_type
        self.focus = self.accessible = None
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="decktil-editor")
        self.future = None
        self.next_at = 0

    def _read(self, panel_xid):
        from inline_state import sample
        from accessible_inline import AccessibleInlineState
        if self.focus is None:
            self.focus = self.focus_type()
            self.accessible = AccessibleInlineState()
        self.focus.panel_xid = panel_xid
        owner = self.focus.target(allow_inline=True)
        if owner is None:
            return None, None
        result = sample(owner)
        if result is None:
            result = self.accessible.sample(owner)
        return owner, result

    def poll(self, now, panel_xid):
        observation = None
        if self.future is not None and self.future.done():
            future, self.future = self.future, None
            try:
                owner, state = future.result()
            except Exception:
                LOG.debug("Steam editor observation unavailable", exc_info=True)
                owner = state = None
            self.next_at = (self.started + .15 if owner is not None and state is not None else now + .55)
            if now - self.started <= MAX_OBSERVATION_AGE:
                observation = owner, state, self.started
        if self.future is None and now >= self.next_at:
            self.started = now
            self.future = self.worker.submit(self._read, panel_xid)
        return observation

    def close(self):
        self.worker.shutdown(wait=False, cancel_futures=True)


class AutoKeyboard:
    def __init__(self, panel, request, claim=lambda: True):
        self.panel = panel
        self.request = request
        self.claim = claim
        self.field = self.bound = self.muted = self.attempted = None
        self.user_closed_at = None
        self.close_sent_at = None
        self.retried = False
        self.confirmed_at = 0
        self.field_transition = None
        self.activation_field = None
        self.a_down = None
        self.a_released_at = None
        self.preview = None
        self.left_touch_at = None
        self.space_committed = None
        self.left_press_position = self.space_origin = None
        self.space_cancelled_at = None
        self.recovered_at = None
        panel.select_space = self.select_space

    def select_space(self, now, *, probe_serial=None):
        panel = self.panel
        field = self.bound
        if (self.left_touch_at is None or now - self.left_touch_at > .35 or not search_identity(field)
                or panel.target != field[0] or now - self.confirmed_at > 1.5
                or (panel.native_handoff and self.field_transition is None)):
            return False
        if not panel.focus.matches(field[0]) or panel.focus.native_visible():
            return True
        result = recover(field)
        if result not in ('focused', 'restored'):
            # A different target must never receive a pending Space. If CEF
            # is unavailable, ordinary non-transition input still has its guard.
            return result == 'blocked' or panel.native_handoff
        if not panel.focus.matches(field[0]) or panel.focus.native_visible():
            return True
        self.activation_field = field
        self.confirmed_at = now
        self.recovered_at = now
        self.field_transition = None
        if probe_serial is not None:
            panel.frames.confirm(probe_serial, 1, 0, now)
        if panel.native_handoff:
            panel.resume_editor(now)
        panel.deliver_space(now)
        # Steam's independent navigation event may arrive just after our key.
        # In that order only restore focus; the Space has already been typed.
        self.space_committed = (field, panel.token, now)
        self.space_origin = self.left_press_position
        return True

    def recent_editor(self, now):
        return self.activation_field if now - self.confirmed_at <= .4 else None

    def prediction_editor(self, now):
        # The shorter chord-preview window is too narrow for the accessibility
        # fallback. Any sample of a different or unknown field clears this ID.
        return self.activation_field if now - self.confirmed_at <= MAX_OBSERVATION_AGE else None

    def report(self, packet, now):
        # Only a fresh physical A edge in a recently verified ordinary editor
        # may cover Steam's opening animation. Never send a Steam command here.
        if packet[8] & 0xfd or packet[9] or packet[10] & 4:
            self.space_cancelled_at = now
        if self.space_committed and (struct.unpack_from('<H', packet, 46)[0] >= 8200
                                     or any(abs(axis) > 8192 for axis in struct.unpack_from('<hhhh', packet, 48))):
            self.space_cancelled_at = now
        if (not self.panel.visible
                or (self.space_cancelled_at is not None and now - self.space_cancelled_at < .25)):
            self.left_touch_at = self.space_committed = None
            self.left_press_position = self.space_origin = None
        elif packet[10] & 8:
            self.left_touch_at = now
            position = struct.unpack_from('<hh', packet, 16)
            force = struct.unpack_from('<H', packet, 56)[0]
            trigger = struct.unpack_from('<H', packet, 44)[0]
            held = force > 350 or packet[10] & 2 or trigger >= 4200 or packet[8] & 2
            if self.space_origin and max(abs(x-y) for x, y in zip(position, self.space_origin)) > 1600:
                self.space_committed = self.space_origin = None
            self.left_press_position = position if held else None
            if not held:
                self.space_origin = None
        else:
            self.left_press_position = self.space_origin = None
        down = bool(packet[8] & 0x80)
        previous, self.a_down = self.a_down, down
        if previous is True and not down:
            self.a_released_at = now
        if not down or previous is not False or packet[9] & 0x20:
            return
        panel = self.panel
        field = self.recent_editor(now)
        if (field is None or not panel.ready
                or panel.native_handoff or panel.show_pending or not self.claim()):
            return
        owner = field[0]
        was_visible = panel.visible and panel.target == owner
        if was_visible:
            if panel.focus.target(allow_inline=True) != owner or panel.focus.native_visible():
                return
            panel.pause_for_native(now)
        elif panel.target is not None or not panel.open_for(owner, now, covering_inline=True):
            return
        self.bound = field
        self.preview = field, was_visible, now
        self.a_released_at = None
        panel.handoff_deadline = now + .8

    def tick(self, now):
        if self.bound and now - self.confirmed_at > 2.5:
            if self.panel.target == self.bound[0]:
                self.panel.stop()
            self.bound = None

    def dismiss_inline(self, owner, now):
        # A new observation of the same ordinary field is required for each
        # request. Leave Steam in charge if the cover never acknowledges show.
        if not self.panel.visible:
            return
        if self.close_sent_at is not None and (self.retried or now - self.close_sent_at < .65):
            return
        if self.panel.focus.target(allow_inline=True) != owner or self.panel.focus.native_visible():
            self.panel.stop()
            return
        self.retried = self.close_sent_at is not None
        self.close_sent_at = now
        if self.request('close'):
            LOG.info("Requested inline Steam keyboard dismissal with Decktil displayed")
        else:
            self.panel.stop()

    def observe(self, owner, state, now, *, sampled_at=None):
        panel = self.panel
        if sampled_at is None:
            sampled_at = now
        if self.preview is not None and sampled_at < self.preview[2]:
            return
        if self.recovered_at is not None and sampled_at < self.recovered_at:
            return
        self.activation_field = None
        if owner is not None and not panel.ready:
            panel.warm()
        if panel.user_closed_at != self.user_closed_at:
            self.user_closed_at = panel.user_closed_at
            self.muted = self.field
            self.bound = None
            self.attempted = None
            self.preview = None
        other = state is not None and state[0] == 'other'
        if (other and self.bound and self.bound[0] == owner
                and (panel.visible or (self.preview and panel.show_pending))):
            completed, self.space_committed = self.space_committed, None
            held = bool(completed and self.space_origin and self.left_press_position
                        and self.left_touch_at is not None and now - self.left_touch_at < .2)
            if (completed and completed[:2] == (self.bound, panel.token) and (held or now - completed[2] < .55)
                    and panel.focus.matches(owner) and not panel.focus.native_visible()
                    and recover(self.bound, held=held) in ('focused', 'restored')):
                if held:
                    self.space_committed = completed
                self.recovered_at = now
                self.activation_field = self.bound
                self.confirmed_at = now
                self.field_transition = None
                if panel.native_handoff:
                    panel.resume_editor(now)
                return
            # When A opens the OSK, Steam's navigation node briefly gets the
            # DOM focus within the same window. Freeze output immediately.
            if self.field_transition is None:
                self.field_transition = now + .65
                if not panel.native_handoff:
                    panel.pause_for_native(now)
                if search_identity(self.bound) and not self.preview:
                    panel.probe_space = True
                    if panel.last_report is not None:
                        panel.frames.feed(panel.last_report, now)
            if now < self.field_transition:
                return
        if owner is None or other:
            self.space_committed = None
            if panel.native_handoff or (self.bound is not None and panel.target == self.bound[0]):
                panel.stop()
            self.field = self.bound = self.muted = self.attempted = None
            self.field_transition = None
            self.preview = None
            return
        if state is None:  # No positive evidence of the editor or a keyboard.
            return
        kind, key = state
        was_transition = self.field_transition is not None
        self.field_transition = None
        field = owner, key
        self.confirmed_at = sampled_at
        if kind == 'editor':
            self.activation_field = field
        if self.preview is not None:
            expected, was_visible, _ = self.preview
            if expected == field:
                if kind == 'inline':
                    self.muted = None
                elif kind == 'editor' and (self.a_released_at is None or sampled_at < self.a_released_at + .18):
                    return
                elif not was_visible:
                    if panel.native_handoff:
                        panel.stop()
                    self.muted = field
            self.preview = None
        just_closed = (self.user_closed_at is not None and self.panel.user_closed_owner == owner
                       and now - self.user_closed_at < 1.25)
        if field != self.field:
            if self.bound and panel.native_handoff and self.bound != field:
                panel.stop()
            if self.bound and self.bound[0] == owner and panel.target == owner:
                self.bound = field
            else:
                self.bound = None
            self.muted = field if self.field is None and just_closed else None
            self.attempted = None
            self.field = field
        if not self.claim():
            return
        if kind == 'inline':
            if self.attempted == field:
                if panel.native_handoff and panel.target == owner and self.bound == field:
                    self.dismiss_inline(owner, now)
                return
            if self.muted == field and just_closed:
                # Steam may raise its own panel on the same closing chord.
                if panel.focus.target(allow_inline=True) != owner or panel.focus.native_visible():
                    return
                self.attempted = field
                self.request('close')
                return
            if not panel.ready:
                return
            self.attempted = field
            self.muted = None
            self.close_sent_at = None
            self.retried = False
            if panel.target == owner and (panel.show_pending or panel.visible):
                if not panel.native_handoff:
                    panel.pause_for_native(now)
            else:
                if panel.target is not None:
                    panel.stop()
                if not panel.open_for(owner, now, covering_inline=True):
                    return
            self.bound = field
            panel.handoff_deadline = now + 2.5
            self.dismiss_inline(owner, now)
            return
        if kind != 'editor':
            return
        was_inline = self.attempted == field
        self.attempted = None
        if panel.native_handoff:
            if ((self.bound == field or was_inline or was_transition)
                    and panel.focus.matches(owner) and not panel.focus.native_visible()):
                panel.resume_editor(now)
                self.bound = field
                LOG.info("Resumed Decktil in the original Steam text field")
                return
            panel.stop()
        if self.muted == field or panel.target is not None:
            return
        if not was_inline and (panel.stock_open is True or now < panel.native_grace_until):
            return
        if not panel.focus.matches(owner):
            return
        if not panel.ready:
            return
        if panel.open_for(owner, now):
            self.bound = field
            LOG.info("Decktil opened for the focused Steam text field")
        else:
            self.muted = field
