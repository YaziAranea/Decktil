"""Exercise the two keyboard actions and the Steam popup race without a desktop."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import chord


class KeyboardActions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(patch.dict(os.environ, XDG_SESSION_TYPE="x11"))
        self.enterContext(patch.object(chord.dbus, "Interface", return_value=Mock()))
        self.enterContext(patch.object(chord.mapping, "running_game", return_value=False))
        self.enterContext(patch.object(chord.GLib, "timeout_add", return_value=10))
        self.now = self.enterContext(patch.object(chord.time, "monotonic", return_value=10.0))
        self.steam = self.enterContext(patch.object(chord, "_steam_request"))
        self.action = chord.SteamChord(Mock())
        self.action.enabled = True
        self.action.mode_record = self.root / "mode"
        self.state = {"available": True, "mode": 1, "visible": False, "activeClientSupportsTextInput": True}
        self.action._state = lambda: self.state.copy()
        self.action._desktop_x_enabled = Mock(return_value=True)
        self.action._allow_mouse_activation = Mock()
        self.action.update(False, False)

    def test_alone_x_opens_edits_then_ignores_steam_echo_after_empty_close(self):
        self.assertFalse(self.action.update(False, True))
        self.assertTrue(self.action.want_open)
        self.action._advance()
        self.action.panel.forceActivate.assert_called_once()
        self.state["visible"] = True
        self.action._advance()
        # Holding the opening press through activation cannot start erasing.
        self.assertFalse(self.action.update(False, True))
        self.action.update(False, False)
        self.action._desktop_opened()
        self.action._advance()
        self.assertTrue(self.action.update(False, True))
        self.assertIsNone(self.action.stage)
        self.assertFalse(self.action.update(False, True))
        self.action._desktop_opened()
        self.assertIsNone(self.action.stage)
        self.action._request(False)  # CloseOnEmpty accepted this edit edge.
        self.action.update(False, False)
        self.state["visible"] = False
        self.action._advance()
        self.action._desktop_opened()
        self.assertIsNone(self.action.stage)
        self.assertFalse(self.action.want_open)
        self.action.panel.forceActivate.assert_called_once()

    def test_steam_log_before_bare_x_does_not_erase_opening_press(self):
        self.action._desktop_opened()
        self.state["visible"] = True
        self.action._advance()
        self.assertFalse(self.action.update(False, True))
        self.assertTrue(self.action.want_open)
        self.action.update(False, False)
        self.now.return_value += 1
        self.action._desktop_opened()
        self.action._advance()
        self.assertTrue(self.action.update(False, True))

    def test_second_x_during_cold_start_keeps_open_request(self):
        self.action.update(False, True)
        self.action.update(False, False)
        self.assertFalse(self.action.update(False, True))
        self.assertTrue(self.action.want_open)

    def test_physical_toggle_and_delayed_echo(self):
        for was_visible in (True, False):
            with self.subTest(was_visible=was_visible):
                self.state["visible"] = was_visible
                self.action.update(True, True)
                self.action.update(False, False)
                self.assertEqual(self.action.want_open, not was_visible)
                self.state["visible"] = not was_visible
                self.action._advance()
                self.action._desktop_opened()
                self.assertEqual(self.action.want_open, not was_visible)
                self.action._advance()

    def test_log_before_physical_toggle(self):
        self.action._desktop_opened()
        self.state["visible"] = True
        self.action._advance()
        self.now.return_value = 10.05
        self.action.update(True, True)
        self.assertTrue(self.action.want_open)

    def test_same_batch_open_close_still_shows_plasma(self):
        log = self.root / "logs/controller_ui.txt"
        log.parent.mkdir()
        log.write_bytes(b"")
        with patch.object(chord.mapping, "ROOT", self.root), patch.object(chord.mapping, "steam_pid", return_value=123):
            self.action.poll_desktop_actions()
            log.write_bytes(b"[00] Set OSK active 1 and appid 413080\r\n[00] Set OSK active 0 and appid 769\r\n")
            self.action.poll_desktop_actions()
        self.assertTrue(self.action.want_open)
        self.assertFalse(self.action.stock_open)
        self.action._advance()
        self.action.panel.forceActivate.assert_called_once()
        self.steam.assert_called_once_with("close")
        self.assertEqual(self.action.last_desktop_osk, 10.0)

    def test_alone_x_leaves_other_bindings_and_non_editors(self):
        self.state["activeClientSupportsTextInput"] = False
        self.action.update(False, True)
        self.assertIsNone(self.action.stage)
        self.action.update(False, False)
        self.state["activeClientSupportsTextInput"] = True
        self.action._desktop_x_enabled.return_value = False
        self.action.update(False, True)
        self.assertIsNone(self.action.stage)
        self.action.update(False, False)
        with patch.object(chord.mapping, "running_game", return_value=True):
            self.action.update(True, True)
        self.assertIsNone(self.action.stage)

    def test_first_panel_can_finish_loading_after_two_seconds(self):
        self.action._request(True)
        self.now.return_value = 12.4
        self.action._advance()
        self.assertEqual(self.action.stage, "activate")
        self.state["visible"] = True
        self.action._advance()
        self.assertIsNone(self.action.stage)
        self.action._request(False)
        self.assertAlmostEqual(self.action.deadline, 14.2)
        self.state["visible"] = False
        self.action._advance()
        self.action._request(True)
        self.assertAlmostEqual(self.action.deadline, 14.2)

    def test_steam_never_returns_on_overlapping_windows_or_timeout(self):
        self.action._request(True)
        with patch.object(chord, "_steam_keyboard", return_value="42"):
            self.action.reconcile(True)
        self.now.return_value = 15
        with self.assertLogs(chord.log, level="WARNING"):
            self.action._advance()
        self.steam.assert_called_once_with("close")
        self.action.properties.Set.assert_not_called()

    def test_default_x_binding(self):
        config = '''"controller_mappings" {
          "controller_type" "controller_neptune"
          "group" {"id" "3" "inputs" {"button_x" {"activators" {"Full_Press" {
             "bindings" {"binding" "controller_action SHOW_KEYBOARD, , "}}}}}}
          "group" {"id" "9" "inputs" {"button_x" {
              "activators" {"Full_Press" {"bindings" {"binding" "controller_action SHOW_KEYBOARD, , "}}}
          }}}
          "preset" {"id" "0" "group_source_bindings" {"9" "button_diamond active"}}
        }'''
        span = chord.mapping.keyboard_x_binding(config)
        self.assertEqual(config[slice(*span)], '"binding" "controller_action SHOW_KEYBOARD, , "')
        saved = self.root / "desktop"
        saved.write_text(config)
        derived = chord.mapping.inherited_template(saved)
        self.assertEqual(derived, config[:span[0]] + config[span[1]:])
        self.assertEqual(derived.count("SHOW_KEYBOARD"), 1)
        self.assertEqual(saved.read_text(), config)
        self.assertFalse(chord.mapping.keyboard_x_binding(config.replace("SHOW_KEYBOARD", "OTHER")))
        self.assertFalse(chord.mapping.keyboard_x_binding(config.replace("diamond active", "diamond inactive")))
        self.assertFalse(chord.mapping.keyboard_x_binding(config.replace("Full_Press", "Long_Press")))
        custom = config.replace('"bindings" {', '"bindings" {"binding" "key_press ESCAPE" ')
        saved.write_text(custom)
        self.assertEqual(chord.mapping.inherited_template(saved), custom)

    def test_temporary_profile_inherits_desktop_b(self):
        config = '''"controller_mappings" { "controller_type" "controller_neptune"
          "group" {"id" "1" "inputs" {
              "button_b" {"activators" {"Full_Press" {"bindings" {"binding" "key_press ESCAPE, , "}}}}
              "button_a" {"activators" {"Full_Press" {"bindings" {"binding" "key_press ESCAPE, , "}}}}
          }}
          "preset" {"id" "0" "group_source_bindings" {"1" "button_diamond active"}}
        }'''
        source = self.root / "desktop"
        for layout in (config, config.replace("ESCAPE", "RETURN"), config.replace("Full_Press", "Long_Press")):
            source.write_text(layout)
            self.assertEqual(chord.mapping.inherited_template(source), layout)
            self.assertEqual(source.read_text(), layout)


if __name__ == "__main__":
    unittest.main()
