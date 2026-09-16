import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import HTTPServer

import fbxstat_web


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


class TestHandler(unittest.TestCase):
    def setUp(self):
        self._orig_html_path = fbxstat_web.DASHBOARD_HTML_PATH
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
        os.unlink(self._tmp_html.name)

    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, resp.getheader("Content-Type"), body

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

    def test_api_history_returns_points(self):
        status, content_type, body = self._get("/api/history")
        data = json.loads(body)
        self.assertEqual(data["points"], [{"t": 1, "wan_down_bps": 800.0}])

    def test_unknown_path_returns_404(self):
        status, _, _ = self._get("/nope")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
