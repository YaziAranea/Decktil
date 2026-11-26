import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from kde_layout import KdeLayout, SYNC_LOCK


class FakeLayouts:
    def __init__(self, changed, groups, active):
        self.changed = changed
        self.groups = tuple((group, "", group.upper()) for group in groups)
        self.active = active
        self.writes = []
        self.closed = False
        self.defer = False
        self.refuse = False

    def state(self):
        return self.groups, self.active

    def set(self, index):
        self.writes.append(index)
        if self.refuse:
            return False
        self.active = index
        if not self.defer:
            self.changed(index)
        return True

    def external(self, index):
        self.active = index
        self.changed(index)

    def close(self):
        self.closed = True


class KdeLayoutTests(unittest.TestCase):
    def setUp(self):
        self.runtime = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, XDG_RUNTIME_DIR=self.runtime))
        self.lock = Path(self.runtime) / SYNC_LOCK

    def helper_can_run(self):
        return subprocess.run(["/usr/bin/flock", "--nonblock", self.lock, "/usr/bin/true"],
                              check=False).returncode == 0

    def open(self, groups=("ru", "us"), active=0):
        def backend(changed):
            self.service = FakeLayouts(changed, groups, active)
            return self.service
        self.foreground = True
        self.layout = KdeLayout(lambda: self.foreground, backend=backend)
        self.addCleanup(self.layout.close)
        return self.layout

    def test_open_read_and_close_never_switch_the_layout(self):
        layout = self.open(("us", "ru"))
        self.assertEqual(layout.locale(), "en_US")
        self.assertTrue(layout.select("en_US"))
        layout.close()
        self.assertTrue(self.service.closed)
        self.assertEqual(self.service.writes, [])

    def test_switch_uses_group_name_and_restores_actual_original_only_once(self):
        layout = self.open(("us", "de", "ru"), active=1)
        self.assertIsNone(layout.locale())
        self.assertTrue(layout.select("ru_RU"))
        self.assertEqual(layout.locale(), "ru_RU")
        self.assertTrue(layout.select("en_US"))
        self.assertEqual(layout.locale(), "en_US")
        layout.close()
        layout.close()
        self.assertEqual(self.service.writes, [2, 0, 1])

    def test_switch_back_to_original_needs_no_extra_write(self):
        layout = self.open()
        self.service.defer = True
        self.assertTrue(layout.select("en_US"))
        self.assertTrue(layout.select("ru_RU"))
        self.service.changed(1)
        self.service.changed(0)
        layout.close()
        self.assertEqual(self.service.writes, [1, 0])

    def test_focus_loss_prevents_select_and_restore(self):
        layout = self.open()
        self.foreground = False
        self.assertFalse(layout.select("en_US"))
        self.foreground = True
        self.assertTrue(layout.select("en_US"))
        self.foreground = False
        layout.close()
        self.assertEqual(self.service.writes, [1])

    def test_user_switch_away_and_back_wins(self):
        layout = self.open()
        self.assertTrue(layout.select("en_US"))
        self.service.external(0)
        self.service.external(1)
        layout.close()
        self.assertEqual(self.service.writes, [1])

    def test_readback_detects_external_change_before_signal_arrives(self):
        layout = self.open(("us", "ru", "de"))
        self.assertTrue(layout.select("ru_RU"))
        self.service.active = 2
        layout.close()
        self.assertEqual(self.service.writes, [1])

    def test_reordered_or_replaced_groups_cancel_restoration(self):
        layout = self.open()
        self.assertTrue(layout.select("en_US"))
        self.service.groups = tuple(reversed(self.service.groups))
        layout.close()
        self.assertEqual(self.service.writes, [1])

    def test_rejected_locale_ambiguous_group_and_unavailable_group(self):
        layout = self.open(("de", "ru", "ru"))
        for locale in ("de_DE", "ru_RU", "en_US"):
            with self.subTest(locale=locale):
                self.assertFalse(layout.select(locale))
        self.assertEqual(self.service.writes, [])

    def test_refused_layout_creates_no_restore_obligation(self):
        layout = self.open()
        self.service.refuse = True
        self.assertFalse(layout.select("en_US"))
        layout.close()
        self.assertEqual(self.service.active, 0)
        self.assertEqual(self.service.writes, [1])

    def test_helper_excluded_from_open_through_restore_and_released_on_close(self):
        self.assertTrue(self.helper_can_run())
        layout = self.open()
        self.assertFalse(self.helper_can_run())
        seen = []
        set_group = self.service.set

        def write_group(index):
            seen.append(self.helper_can_run())
            return set_group(index)

        with patch.object(self.service, "set", side_effect=write_group):
            self.assertTrue(layout.select("en_US"))
            layout.close()
        self.assertEqual(seen, [False, False])
        self.assertTrue(self.helper_can_run())

    def test_contended_open_does_not_change_layout_select_retries(self):
        with self.lock.open("a") as helper:
            fcntl.flock(helper, fcntl.LOCK_EX)
            layout = self.open()
            self.assertEqual(layout.locale(), "ru_RU")
            self.assertFalse(layout.select("en_US"))
            self.assertEqual(self.service.writes, [])
        self.assertTrue(layout.select("en_US"))
        self.assertFalse(self.helper_can_run())
        layout.close()
        self.assertEqual(self.service.writes, [1, 0])
        self.assertTrue(self.helper_can_run())

    def test_failed_dbus_read_releases_lock(self):
        layout = self.open()
        with patch.object(self.service, "state", side_effect=ValueError):
            self.assertIsNone(layout.locale())
            self.assertTrue(self.helper_can_run())
        self.assertEqual(layout.locale(), "ru_RU")
        self.assertFalse(self.helper_can_run())

    def test_unsafe_lock_target_cannot_switch_or_block(self):
        target = Path(self.runtime) / "other"
        target.touch()
        self.lock.symlink_to(target)
        layout = self.open()
        self.assertEqual(layout.locale(), "ru_RU")
        self.assertFalse(layout.select("en_US"))
        self.assertEqual(self.service.writes, [])
        with target.open("r") as file:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)


if __name__ == "__main__":
    unittest.main()
