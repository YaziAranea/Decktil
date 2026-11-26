import unittest
from unittest.mock import patch

import inline_state as inline


class InlineStateTests(unittest.TestCase):
    def test_discovery_accepts_inherited_steam_page_without_exporting_url(self):
        page = {"type": "page", "id": "DEAD123",
                "url": "about:blank?browserID=fixture",
                "webSocketDebuggerUrl": "ws://127.0.0.1:8080/devtools/page/DEAD123"}
        self.assertEqual(inline._page_id(page), "DEAD123")
        for url in ("https://steamcommunity.com/", "https://steamloopback.host.evil/",
                    "https://steamloopback.host@other.invalid/", "about:srcdoc"):
            with self.subTest(url=url):
                self.assertIsNone(inline._page_id({**page, "url": url}))
        for change in ({"type": "iframe"}, {"id": "../a"}, {"id": "X"},
                       {"webSocketDebuggerUrl": "ws://other.invalid/devtools/page/DEAD123"}):
            self.assertIsNone(inline._page_id({**page, **change}))

    def test_identity_uses_only_document_epoch_and_structural_path(self):
        result = ["inline", 1234, "search", [3, 1]]
        self.assertEqual(inline._result("page", result),
                         ("inline", ("page", 1234, "search", (3, 1))))
        original = inline._result("page", result)[1]
        self.assertEqual(original, inline._result("page", ["editor", *result[1:]])[1])
        self.assertNotEqual(original, inline._result("page", ["editor", 1235, *result[2:]])[1])
        self.assertNotEqual(original, inline._result("page", ["editor", 1234, "search", [4, 1]])[1])

    def test_invalid_or_unsafe_results_cannot_arm_a_handoff(self):
        self.assertEqual(inline._result("page", ["other"]), ("other", None))
        for data in (None, [], ["other", "field"], ["inline", 10, "password", [0]],
                     ["inline", True, "text", [0]], ["inline", 10, "textarea", [-1]],
                     ["inline", 10, "textarea", ["id"]], ["inline", 10, "textarea", [0] * 49]):
            with self.subTest(data=data):
                self.assertIsNone(inline._result("page", data))

    def test_absent_marker_does_not_probe_network(self):
        with patch.object(inline, "_observe") as observer, patch.object(inline.Path, "lstat", side_effect=OSError):
            self.assertIsNone(inline.sample(407))
            observer.assert_not_called()

    def test_untrusted_window_cannot_observe_page(self):
        marker = type("Marker", (), {"st_mode": 0o100600, "st_uid": inline.os.getuid()})()
        with patch.object(inline.Path, "lstat", return_value=marker), \
             patch.object(inline, "_props", return_value={"_NET_WM_PID": "45"}), \
             patch.object(inline, "_trusted", return_value=False), \
             patch.object(inline, "_observe") as observer:
            self.assertIsNone(inline.sample(407))
            observer.assert_not_called()

    def test_changed_or_missing_listener_invalidates_result(self):
        marker = type("Marker", (), {"st_mode": 0o100600, "st_uid": inline.os.getuid()})()
        with patch.object(inline.Path, "lstat", return_value=marker), \
             patch.object(inline, "_props", return_value={"_NET_WM_PID": "45"}), \
             patch.object(inline, "_trusted", return_value=True), \
             patch.object(inline, "_listener", side_effect=[None, "34", "56"]), \
             patch.object(inline, "_known_skin", return_value=object()), \
             patch.object(inline, "_observe", return_value=("other", None)) as observer:
            self.assertIsNone(inline.sample(407))
            observer.assert_not_awaited()
            self.assertIsNone(inline.sample(407))
            observer.assert_awaited_once()
