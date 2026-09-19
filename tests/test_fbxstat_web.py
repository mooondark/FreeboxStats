import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import HTTPServer
from unittest import mock

import fbxstat_web
from freebox_api import AuthRequired


class TestHistory(unittest.TestCase):
    def test_add_respects_maxlen(self):
        history = fbxstat_web.History(maxlen=3)
        for i in range(5):
            history.add({"t": i})
        self.assertEqual([p["t"] for p in history.to_list()], [2, 3, 4])


class TestArgParser(unittest.TestCase):
    def test_default_interval_is_one_second(self):
        args = fbxstat_web.build_arg_parser().parse_args([])
        self.assertEqual(args.interval, 1.0)

    def test_interval_can_be_overridden(self):
        args = fbxstat_web.build_arg_parser().parse_args(["--interval", "5"])
        self.assertEqual(args.interval, 5.0)

    def test_default_host_is_localhost(self):
        args = fbxstat_web.build_arg_parser().parse_args([])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8000)


class TestMainIntervalValidation(unittest.TestCase):
    def test_non_positive_interval_exits_with_error(self):
        with mock.patch.object(fbxstat_web.sys, "argv", ["fbxstat_web.py", "--interval", "0"]):
            with self.assertRaises(SystemExit) as cm:
                fbxstat_web.main()
        self.assertEqual(cm.exception.code, 2)


class TestHandler(unittest.TestCase):
    def setUp(self):
        self._orig_html_path = fbxstat_web.DASHBOARD_HTML_PATH
        self._orig_interval = fbxstat_web.INTERVAL
        self._orig_state_lock = fbxstat_web.STATE_LOCK
        self._orig_state = fbxstat_web.STATE
        self._tmp_html = tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        )
        self._tmp_html.write("<html><body>placeholder</body></html>")
        self._tmp_html.close()
        fbxstat_web.DASHBOARD_HTML_PATH = self._tmp_html.name

        fbxstat_web.INTERVAL = 2.0
        fbxstat_web.STATE_LOCK = threading.Lock()
        fbxstat_web.STATE = {
            "snapshot": {"model": "Freebox v9 (r1)", "wan_down_bps": 800.0},
            "history": fbxstat_web.History(maxlen=10),
        }
        fbxstat_web.STATE["history"].add({"t": 1, "wan_down_bps": 800.0})

        self.server = HTTPServer(("127.0.0.1", 0), fbxstat_web.Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        fbxstat_web.DASHBOARD_HTML_PATH = self._orig_html_path
        fbxstat_web.INTERVAL = self._orig_interval
        fbxstat_web.STATE_LOCK = self._orig_state_lock
        fbxstat_web.STATE = self._orig_state
        os.unlink(self._tmp_html.name)

    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, resp.getheader("Content-Type"), body

    def _post(self, path, body, content_type="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", path, body=body, headers={"Content-Type": content_type})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_post_interval_updates_server_interval(self):
        status, data = self._post("/api/interval", json.dumps({"interval": 5}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {"interval": 5.0})
        self.assertEqual(fbxstat_web.INTERVAL, 5.0)

    def test_post_interval_rejects_value_outside_allowlist(self):
        for bad in (0, 2, -1, 999, "abc", None):
            status, _ = self._post("/api/interval", json.dumps({"interval": bad}))
            self.assertEqual(status, 400, bad)
        self.assertEqual(fbxstat_web.INTERVAL, 2.0)

    def test_post_interval_rejects_malformed_body(self):
        status, _ = self._post("/api/interval", "not json")
        self.assertEqual(status, 400)
        self.assertEqual(fbxstat_web.INTERVAL, 2.0)

    def test_post_interval_requires_json_content_type(self):
        status, _ = self._post("/api/interval", json.dumps({"interval": 5}), content_type="text/plain")
        self.assertEqual(status, 415)
        self.assertEqual(fbxstat_web.INTERVAL, 2.0)

    def test_post_unknown_path_returns_404(self):
        status, _ = self._post("/api/nope", "{}")
        self.assertEqual(status, 404)

    def test_root_serves_dashboard_html(self):
        status, content_type, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/html")
        self.assertIn(b"placeholder", body)

    def test_api_snapshot_includes_interval(self):
        status, content_type, body = self._get("/api/snapshot")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        data = json.loads(body)
        self.assertEqual(data["model"], "Freebox v9 (r1)")
        self.assertEqual(data["interval"], 2.0)

    def test_api_snapshot_includes_timestamp(self):
        fbxstat_web.STATE["snapshot_t"] = 12345.0
        status, content_type, body = self._get("/api/snapshot")
        data = json.loads(body)
        self.assertEqual(data["t"], 12345.0)

    def test_api_history_returns_points(self):
        status, content_type, body = self._get("/api/history")
        data = json.loads(body)
        self.assertEqual(data["points"], [{"t": 1, "wan_down_bps": 800.0}])

    def test_unknown_path_returns_404(self):
        status, _, _ = self._get("/nope")
        self.assertEqual(status, 404)


class _StopLoop(Exception):
    """Sentinel used to break out of collect_loop's infinite loop in tests."""


class TestCollectLoop(unittest.TestCase):
    def setUp(self):
        self._orig_state_lock = fbxstat_web.STATE_LOCK
        self._orig_state = fbxstat_web.STATE
        fbxstat_web.STATE_LOCK = threading.Lock()
        fbxstat_web.STATE = {"snapshot": None, "history": fbxstat_web.History(maxlen=10)}

    def tearDown(self):
        fbxstat_web.STATE_LOCK = self._orig_state_lock
        fbxstat_web.STATE = self._orig_state

    def test_reauth_failure_is_caught_and_loop_keeps_ticking(self):
        snapshot = {
            "wan_down_bps": 1.0,
            "wan_up_bps": 2.0,
            "sensors": {},
            "fans": {},
        }
        with mock.patch.object(
            fbxstat_web, "open_session", side_effect=["tok-initial", RuntimeError("re-auth failed"), "tok-recovered"]
        ) as mock_open_session, mock.patch.object(
            fbxstat_web, "fetch_snapshot", side_effect=[AuthRequired(), snapshot, snapshot]
        ) as mock_fetch, mock.patch.object(
            fbxstat_web.time, "sleep", side_effect=[None, None, _StopLoop()]
        ) as mock_sleep:
            with self.assertRaises(_StopLoop):
                fbxstat_web.collect_loop("app-token")

        # The re-auth RuntimeError must not have propagated: the sentinel did.
        self.assertEqual(mock_open_session.call_count, 3)
        self.assertEqual(mock_fetch.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 3)

    def test_snapshot_t_is_recorded_alongside_snapshot(self):
        snapshot = {"wan_down_bps": 1.0, "wan_up_bps": 2.0, "sensors": {}, "fans": {}}
        with mock.patch.object(fbxstat_web, "open_session", return_value="tok"), \
                mock.patch.object(fbxstat_web, "fetch_snapshot", return_value=snapshot), \
                mock.patch.object(fbxstat_web.time, "time", return_value=999.0), \
                mock.patch.object(fbxstat_web.time, "sleep", side_effect=_StopLoop()):
            with self.assertRaises(_StopLoop):
                fbxstat_web.collect_loop("app-token")

        self.assertEqual(fbxstat_web.STATE["snapshot_t"], 999.0)


if __name__ == "__main__":
    unittest.main()
