import http.client
import json
import os
import socket
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
    def test_default_interval_is_three_seconds(self):
        args = fbxstat_web.build_arg_parser().parse_args([])
        self.assertEqual(args.interval, 3.0)
        self.assertIn(args.interval, fbxstat_web.ALLOWED_INTERVALS)

    def test_interval_can_be_overridden(self):
        args = fbxstat_web.build_arg_parser().parse_args(["--interval", "5"])
        self.assertEqual(args.interval, 5.0)

    def test_browser_opens_by_default_and_can_be_disabled(self):
        parser = fbxstat_web.build_arg_parser()
        self.assertFalse(parser.parse_args([]).no_browser)
        self.assertTrue(parser.parse_args(["--no-browser"]).no_browser)

    def test_browser_url_replaces_wildcard_host(self):
        self.assertEqual(fbxstat_web.browser_url("0.0.0.0", 8000), "http://127.0.0.1:8000")
        self.assertEqual(fbxstat_web.browser_url("::", 8000), "http://127.0.0.1:8000")
        self.assertEqual(fbxstat_web.browser_url("192.168.1.10", 9000), "http://192.168.1.10:9000")

    def test_browser_url_brackets_ipv6_literal(self):
        self.assertEqual(fbxstat_web.browser_url("::1", 8000), "http://[::1]:8000")
        self.assertEqual(fbxstat_web.browser_url("2a01:e0a::1", 9000), "http://[2a01:e0a::1]:9000")

    def test_default_host_is_localhost(self):
        args = fbxstat_web.build_arg_parser().parse_args([])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8000)


class TestLanUrls(unittest.TestCase):
    def _patched(self, ipv4, ipv6):
        return mock.patch.multiple(
            fbxstat_web, primary_ipv4=lambda: ipv4, link_local_ipv6=lambda: ipv6
        )

    def test_ipv4_wildcard_lists_only_the_ipv4_url(self):
        with self._patched("192.168.1.96", ["fe80::1"]):
            self.assertEqual(
                fbxstat_web.lan_urls("0.0.0.0", 8000),
                [("IPv4", "http://192.168.1.96:8000")],
            )

    def test_ipv6_wildcard_lists_ipv4_and_link_local_ipv6(self):
        with self._patched("192.168.1.96", ["fe80::1", "fe80::2"]):
            self.assertEqual(
                fbxstat_web.lan_urls("::", 8000),
                [
                    ("IPv4", "http://192.168.1.96:8000"),
                    ("IPv6", "http://[fe80::1]:8000"),
                    ("IPv6", "http://[fe80::2]:8000"),
                ],
            )

    def test_specific_or_loopback_host_lists_nothing(self):
        with self._patched("192.168.1.96", ["fe80::1"]):
            self.assertEqual(fbxstat_web.lan_urls("127.0.0.1", 8000), [])
            self.assertEqual(fbxstat_web.lan_urls("192.168.1.96", 8000), [])

    def test_missing_ipv4_is_skipped(self):
        with self._patched(None, ["fe80::1"]):
            self.assertEqual(fbxstat_web.lan_urls("::", 8000), [("IPv6", "http://[fe80::1]:8000")])

    def test_link_local_ipv6_keeps_only_unique_fe80_without_zone(self):
        infos = [
            (socket.AF_INET6, 0, 0, "", ("fe80::a%12", 0, 0, 12)),
            (socket.AF_INET6, 0, 0, "", ("fe80::a%13", 0, 0, 13)),
            (socket.AF_INET6, 0, 0, "", ("fe80::b", 0, 0, 0)),
            (socket.AF_INET6, 0, 0, "", ("2a01:e0a::1", 0, 0, 0)),
        ]
        with mock.patch.object(fbxstat_web.socket, "getaddrinfo", return_value=infos):
            with mock.patch.object(fbxstat_web, "IF_INET6_PATH", "/nonexistent"):
                self.assertEqual(fbxstat_web.link_local_ipv6(), ["fe80::a", "fe80::b"])

    def test_link_local_ipv6_survives_lookup_error(self):
        with mock.patch.object(fbxstat_web.socket, "getaddrinfo", side_effect=OSError):
            with mock.patch.object(fbxstat_web, "IF_INET6_PATH", "/nonexistent"):
                self.assertEqual(fbxstat_web.link_local_ipv6(), [])

    def test_link_local_ipv6_reads_proc_net_if_inet6_on_linux(self):
        content = (
            "00000000000000000000000000000001 01 80 10 80       lo\n"
            "fe80000000000000f816a3fffe2b5e6d 02 40 20 80     eth0\n"
            "2a010e0a0a2596a0f816a3fffe2b5e6d 02 40 00 80     eth0\n"
            "fe80000000000000021122fffe334455 05 40 20 80  docker0\n"
        )
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(content)
        self.addCleanup(os.unlink, f.name)
        with mock.patch.object(fbxstat_web.socket, "getaddrinfo", side_effect=OSError):
            with mock.patch.object(fbxstat_web, "IF_INET6_PATH", f.name):
                self.assertEqual(
                    fbxstat_web.link_local_ipv6(),
                    ["fe80::211:22ff:fe33:4455", "fe80::f816:a3ff:fe2b:5e6d"],
                )


def _ipv6_available():
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
            s.bind(("::1", 0))
        return True
    except OSError:
        return False


class TestMakeServer(unittest.TestCase):
    def test_family_follows_host(self):
        server = fbxstat_web.make_server("127.0.0.1", 0)
        self.addCleanup(server.server_close)
        self.assertEqual(server.address_family, socket.AF_INET)

    @unittest.skipUnless(_ipv6_available(), "IPv6 loopback unavailable")
    def test_ipv6_wildcard_serves_both_ipv6_and_ipv4(self):
        server = fbxstat_web.make_server("::", 0)
        self.assertEqual(server.address_family, socket.AF_INET6)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        for host in ("::1", "127.0.0.1"):
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request("GET", "/nope")
            self.assertEqual(conn.getresponse().status, 404, host)
            conn.close()


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
        patcher = mock.patch.object(fbxstat_web, "wait_for_viewer")
        self.wait_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        fbxstat_web.STATE_LOCK = self._orig_state_lock
        fbxstat_web.STATE = self._orig_state

    def test_waits_for_a_viewer_before_every_tick(self):
        snapshot = {"wan_down_bps": 1.0, "wan_up_bps": 2.0, "sensors": {}, "fans": {}}
        with mock.patch.object(fbxstat_web, "open_session", return_value="tok"), \
                mock.patch.object(fbxstat_web, "fetch_snapshot", return_value=snapshot) as mock_fetch, \
                mock.patch.object(fbxstat_web.time, "sleep", side_effect=[None, _StopLoop()]):
            with self.assertRaises(_StopLoop):
                fbxstat_web.collect_loop("app-token")

        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(self.wait_mock.call_count, 2)

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


class TestViewerActivity(unittest.TestCase):
    def setUp(self):
        self._saved = {k: getattr(fbxstat_web, k) for k in ("STATE_LOCK", "STATE", "LAST_ACTIVITY", "IDLE_AFTER")}
        fbxstat_web.STATE_LOCK = threading.Lock()
        fbxstat_web.STATE = {"snapshot": {"model": "x"}, "snapshot_t": 1.0, "history": fbxstat_web.History(maxlen=10)}
        fbxstat_web.STATE["history"].add({"t": 1})
        fbxstat_web.IDLE_AFTER = 15.0
        self.threads = []

    def tearDown(self):
        fbxstat_web.touch()  # release any thread still waiting
        for t in self.threads:
            t.join(timeout=2)
        for k, v in self._saved.items():
            setattr(fbxstat_web, k, v)

    def _wait_in_thread(self):
        done = threading.Event()
        t = threading.Thread(target=lambda: (fbxstat_web.wait_for_viewer(), done.set()), daemon=True)
        t.start()
        self.threads.append(t)
        return done

    def test_touch_records_the_time(self):
        fbxstat_web.LAST_ACTIVITY = 0.0
        fbxstat_web.touch()
        self.assertGreater(fbxstat_web.LAST_ACTIVITY, 0.0)

    def test_does_not_wait_while_a_viewer_is_active(self):
        fbxstat_web.touch()
        self.assertTrue(self._wait_in_thread().wait(1))
        self.assertEqual(fbxstat_web.STATE["snapshot"], {"model": "x"})

    def test_idle_blocks_clears_data_and_a_touch_wakes_it(self):
        fbxstat_web.LAST_ACTIVITY = 0.0
        done = self._wait_in_thread()

        self.assertFalse(done.wait(0.3))
        self.assertIsNone(fbxstat_web.STATE["snapshot"])
        self.assertIsNone(fbxstat_web.STATE["snapshot_t"])
        self.assertEqual(fbxstat_web.STATE["history"].to_list(), [])

        fbxstat_web.touch()
        self.assertTrue(done.wait(2))


class TestHandlerTouches(unittest.TestCase):
    def test_get_requests_count_as_viewer_activity(self):
        fbxstat_web.STATE_LOCK = threading.Lock()
        fbxstat_web.STATE = {"snapshot": None, "history": fbxstat_web.History(maxlen=10)}
        server = HTTPServer(("127.0.0.1", 0), fbxstat_web.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        saved = fbxstat_web.LAST_ACTIVITY
        self.addCleanup(setattr, fbxstat_web, "LAST_ACTIVITY", saved)

        for path in ("/", "/api/snapshot", "/api/history"):
            fbxstat_web.LAST_ACTIVITY = 0.0
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("GET", path)
            conn.getresponse().read()
            conn.close()
            self.assertGreater(fbxstat_web.LAST_ACTIVITY, 0.0, path)


class TestHistoryClear(unittest.TestCase):
    def test_clear_empties_the_buffer_but_keeps_maxlen(self):
        history = fbxstat_web.History(maxlen=2)
        history.add({"t": 1})
        history.clear()
        self.assertEqual(history.to_list(), [])
        for i in range(5):
            history.add({"t": i})
        self.assertEqual([p["t"] for p in history.to_list()], [3, 4])


if __name__ == "__main__":
    unittest.main()
