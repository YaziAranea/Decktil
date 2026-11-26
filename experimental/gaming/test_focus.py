import unittest
from unittest.mock import Mock, patch

import focus


class FocusChecks(unittest.TestCase):
    def test_local_routes_only(self):
        base = "https://steamloopback.host"
        for route in ("/library/home", "/search", "/routes/library/tab/2", "/index.html#/search"):
            self.assertTrue(focus._route(base + route, False), route)
        for route in ("/gameapiosk", "/apprunning", "/keyboard", "/store", "/library/app/123",
                      "/sp.html#/gameapiosk", "/sp.html?appid=769", "/sp.html?IN_GAMEPADUI&route=/keyboard"):
            self.assertFalse(focus._route(base + route, True), route)
        for url in ("https://example.org/search", "http://steamloopback.host/search",
                    "https://steamloopback.host@example.org/search", base + ".example/search"):
            self.assertFalse(focus._route(url, True), url)

    def test_neutral_bootstrap_requires_recognized_universal_search(self):
        for url in ("https://steamloopback.host/sp.html", "https://steamloopback.host/index.html?IN_GAMEPADUI",
                    "https://steamloopback.host/index.html?IN_GAMEPADUI=true"):
            self.assertFalse(focus._route(url, False))
            self.assertTrue(focus._route(url, True))

    def test_result_tabs_require_a_recognized_search_and_a_local_route(self):
        route = "https://steamloopback.host/routes/search/tab/All"
        self.assertTrue(focus._route(route, True))
        self.assertFalse(focus._route(route, False))
        for suffix in ("/game", "%2fpassword", "?redirect=/store"):
            self.assertFalse(focus._route(route + suffix, True))

    def test_gamescope_cardinal_display_name_is_not_server_number(self):
        self.assertTrue(focus._matches_display("12346, 0, 32", ":0"))
        self.assertTrue(focus._matches_display("12346, 0, 32", ":0.0"))
        self.assertFalse(focus._matches_display("12346, 0, 32", ":1"))

    def test_editor_must_be_unique_and_match_the_steam_window_process(self):
        window = dict(WM_CLASS='"steam", "steam"', _NET_WM_PID="567", STEAM_GAME="769")
        root = dict(GAMESCOPE_FOCUSED_WINDOW="332", GAMESCOPE_FOCUSED_APP="769",
                    GAMESCOPE_FOCUSED_APP_GFX="769", GAMESCOPE_FOCUS_DISPLAY="12346, 0, 32",
                    GAMESCOPE_KEYBOARD_FOCUS_DISPLAY="12346, 0, 32", STEAM_GAMES_RUNNING="0")
        def props(where, keys):
            return root if where == ["-root"] else window

        with (patch.dict(focus.os.environ, DISPLAY=":0", GAMESCOPE_WAYLAND_DISPLAY="gamescope-0"),
              patch.object(focus, "running_game", return_value=False),
              patch.object(focus, "_props", props), patch.object(focus, "_trusted", return_value=True),
              patch.object(focus, "_command", return_value="332") as command):
            self.assertEqual(focus._window(), (332, 567))
            window["WM_CLASS"] = '"steamwebhelper", "steam"'
            self.assertEqual(focus._window(), (332, 567))
            window["WM_CLASS"] = '"unrelated", "steam"'
            self.assertIsNone(focus._window())
            window["WM_CLASS"] = '"steamwebhelper", "steam"'
            command.side_effect = ["332", "332\n339"]
            self.assertIsNone(focus._window())
            command.side_effect = None
            root["STEAM_GAMES_RUNNING"] = "1"
            self.assertIsNone(focus._window())
            root["STEAM_GAMES_RUNNING"] = "0"
            root["GAMESCOPE_FOCUSED_APP_GFX"] = "141234"
            self.assertIsNone(focus._window())

    def test_first_chord_requires_clear_recognized_search_metadata(self):
        skin = focus.Skin("search", "wrapper", "keyboard", "buffer", "modal", "standalone")
        target = focus.Editor(1, 2, ":1.2", "/document", "/input", True)
        ref, blocker = (target.bus, target.entry), (target.bus, "/obstruction")
        tree = object.__new__(focus._Tree)
        tree.matches = Mock(side_effect=[[], [ref, blocker]])
        tree.call = Mock(side_effect=[{"class": "search active"}, {"class": "theme modal"}])
        self.assertFalse(tree.clear_to_wait(target, skin))
        tree.matches.side_effect = [[], [ref]]
        tree.call.side_effect = [{"class": "search active"}]
        self.assertTrue(tree.clear_to_wait(target, skin))
        tree.matches.side_effect = [[], [ref] * 257]
        self.assertFalse(tree.clear_to_wait(target, skin))
        tree.matches.side_effect = [[blocker]]
        self.assertFalse(tree.clear_to_wait(target, skin))

    def test_unavailable_source_and_foreign_focus_are_conservative(self):
        self.assertIsNone(focus._skin_from_bundle(b'VirtualKeyboardContainer:"looks-like-steam"'))
        with patch.object(focus, "_window", return_value=None):
            inspector = focus.LibraryFocus()
            self.assertIsNone(inspector.editor())
            self.assertIsNone(inspector.inline(focus.Editor(1, 2, ":1.2", "/doc", "/entry")))
            self.assertEqual(inspector.reason, "no-idle-steam-library-window")


if __name__ == "__main__":
    unittest.main()
