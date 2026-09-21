# tests/test_freebox_api.py
import json
import unittest
from unittest.mock import patch, mock_open, MagicMock

import freebox_api


class TestGetAppToken(unittest.TestCase):
    @patch("freebox_api.os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data=json.dumps({"app_token": "abc123"}))
    def test_reads_existing_token_file(self, mock_file, mock_exists):
        token = freebox_api.get_app_token()
        self.assertEqual(token, "abc123")
        mock_exists.assert_called_once_with(freebox_api.TOKEN_FILE)


class TestOpenSession(unittest.TestCase):
    @patch("freebox_api.http.post")
    @patch("freebox_api.http.get")
    def test_success_returns_session_token(self, mock_get, mock_post):
        mock_get.return_value = MagicMock(json=lambda: {"result": {"challenge": "chal"}})
        mock_post.return_value = MagicMock(json=lambda: {
            "success": True,
            "result": {"session_token": "sess-token"},
        })

        token = freebox_api.open_session("app-token")

        self.assertEqual(token, "sess-token")
        mock_post.assert_called_once()

    @patch("freebox_api.http.post")
    @patch("freebox_api.http.get")
    def test_failure_raises_runtime_error(self, mock_get, mock_post):
        mock_get.return_value = MagicMock(json=lambda: {"result": {"challenge": "chal"}})
        mock_post.return_value = MagicMock(json=lambda: {
            "success": False,
            "error_code": "invalid_token",
        })

        with self.assertRaises(RuntimeError):
            freebox_api.open_session("app-token")


class TestRegisterApp(unittest.TestCase):
    @patch("freebox_api.time.sleep")
    @patch("builtins.open", new_callable=mock_open)
    @patch("freebox_api.http.get")
    @patch("freebox_api.http.post")
    def test_polls_until_granted_then_saves_token(self, mock_post, mock_get, mock_file, mock_sleep):
        mock_post.return_value = MagicMock(json=lambda: {
            "result": {"app_token": "new-token", "track_id": 42},
        })
        mock_get.side_effect = [
            MagicMock(json=lambda: {"result": {"status": "pending"}}),
            MagicMock(json=lambda: {"result": {"status": "granted"}}),
        ]

        token = freebox_api.register_app()

        self.assertEqual(token, "new-token")
        self.assertEqual(mock_get.call_count, 2)
        mock_sleep.assert_called_once()
        mock_file().write.assert_called()

    @patch("freebox_api.time.sleep")
    @patch("freebox_api.http.get")
    @patch("freebox_api.http.post")
    def test_raises_on_timeout(self, mock_post, mock_get, mock_sleep):
        mock_post.return_value = MagicMock(json=lambda: {
            "result": {"app_token": "new-token", "track_id": 42},
        })
        mock_get.return_value = MagicMock(json=lambda: {"result": {"status": "timeout"}})

        with self.assertRaises(RuntimeError):
            freebox_api.register_app()


class TestSharedHttpSession(unittest.TestCase):
    def test_one_persistent_session_is_shared_by_every_module(self):
        import freebox_data
        import requests

        self.assertIsInstance(freebox_api.http, requests.Session)
        self.assertIs(freebox_data.http, freebox_api.http)


if __name__ == "__main__":
    unittest.main()
