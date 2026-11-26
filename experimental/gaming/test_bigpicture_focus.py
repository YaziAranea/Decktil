import unittest
from unittest.mock import Mock, patch

import bigpicture_focus as bp
from focus import ROLE


class WindowChecks(unittest.TestCase):
    def setUp(self):
        self.props = {
            "WM_CLASS": '"steamwebhelper", "steam"',
            "_NET_WM_PID": "7",
            "STEAM_GAME": "769",
            "_NET_WM_STATE": "_NET_WM_STATE_MAXIMIZED_HORZ, _NET_WM_STATE_FULLSCREEN, _NET_WM_STATE_FOCUSED",
            "_NET_WM_WINDOW_TYPE": "_NET_WM_WINDOW_TYPE_NORMAL",
        }
        self.guard = bp.BigPictureFocus()
        self.guard._locked = Mock(return_value=False)
        self.guard._accessible = Mock()
        self.guard._accessible.check.return_value = None
        def mock(name, **args):
            return self.enterContext(patch.object(bp, name, **args))
        self.stack = mock("_stack", return_value=(10, []))
        self.window = mock("_window", return_value=self.props)
        self.command = mock("_command", return_value="10")
        self.trusted = mock("_trusted", return_value=True)
        self.game = mock("running_game", return_value=False)
        self.env = self.enterContext(patch.dict(bp.os.environ, {
            "XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland",
            "DISPLAY": ":0", "GAMESCOPE_WAYLAND_DISPLAY": "",
        }))

    def test_matches_only_current_trusted_focused_fullscreen_owner(self):
        self.assertEqual(self.guard.editor(), 10)
        self.assertTrue(self.guard.matches(10))
        self.assertFalse(self.guard.matches(None))
        self.command.return_value = "11"
        self.assertFalse(self.guard.matches(10))
        self.command.return_value = "10"
        self.trusted.return_value = False
        self.assertIsNone(self.guard.target())

    def test_session_lock_game_and_native_surface(self):
        self.guard._locked.return_value = True
        self.assertIsNone(self.guard.target())
        self.guard._locked.return_value = False
        self.game.return_value = True
        self.assertIsNone(self.guard.target())
        self.game.return_value = False
        with patch.dict(bp.os.environ, GAMESCOPE_WAYLAND_DISPLAY="gamescope-0"):
            self.assertIsNone(self.guard.target())
        self.guard._accessible.check.return_value = "password-field"
        self.assertIsNone(self.guard.target())
        self.assertEqual(self.guard.reason, "password-field")

    def test_dialog_and_overlay_are_not_main_windows(self):
        for change in ({"_NET_WM_WINDOW_TYPE": "_NET_WM_WINDOW_TYPE_DIALOG"},
                       {"_NET_WM_STATE": "_NET_WM_STATE_FULLSCREEN"}, {"WM_TRANSIENT_FOR": "0xa"},
                       {"STEAM_GAME": "5"}, {"WM_CLASS": '"other", "steam"'}, {"STEAM_OVERLAY": "1"}):
            with self.subTest(change=change), patch.dict(self.props, change):
                self.assertIsNone(self.guard.target())

    def test_nonactivating_keyboard_does_not_cover_its_own_target(self):
        self.stack.return_value = (10, [11, 12])
        self.guard.panel_xid = 12
        with patch.object(bp, "_obstruction", return_value=None) as check:
            self.assertTrue(self.guard.matches(10))
            check.assert_called_once_with(10, 11)
            check.return_value = "native-keyboard"
            self.assertFalse(self.guard.matches(10))
            self.assertEqual(self.guard.reason, "native-keyboard")

    def test_only_mapped_secondary_windows_block_delivery(self):
        self.window.return_value = {"WM_TRANSIENT_FOR": "0xa"}
        self.command.return_value = "Map State: IsUnMapped"
        self.assertIsNone(bp._obstruction(10, 11))
        self.command.return_value = "Map State: IsViewable"
        self.assertEqual(bp._obstruction(10, 11), "competing-window")
        self.window.return_value = self.props
        self.command.side_effect = ["Map State: IsViewable", next(iter(bp.STEAM_KEYBOARD_TITLES))]
        self.assertEqual(bp._obstruction(10, 11), "native-keyboard")

    def test_exposed_password_metadata_vetoes_without_requesting_text(self):
        checker = bp.AccessibleBlockers()
        checker.pid, checker.app, checker.tree = 7, (":2.2", "/app"), Mock()
        node = (":2.2", "/field")
        checker.tree.matches.return_value = [node]
        checker.tree.call.side_effect = [ROLE.ENTRY, {"text-input-type": "password"}]
        self.assertEqual(checker.check(7), "password-field")
        self.assertEqual([call.args[2] for call in checker.tree.call.call_args_list], ["GetRole", "GetAttributes"])


if __name__ == "__main__":
    unittest.main()
