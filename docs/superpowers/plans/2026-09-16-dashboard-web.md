# Dashboard web Freebox — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local web dashboard (charts + tables) reusing the same Freebox
data `fbxstat.py` already shows in the terminal, with a 10-minute rolling
history for WAN speed and temperature/fan.

**Architecture:** Extract the Freebox auth code shared by `fbxstat.py` into
`freebox_api.py`. Add a new `freebox_data.py` module with pure, testable
functions that fetch and shape Freebox data into JSON-ready dicts. Add
`fbxstat_web.py`: a background thread polls `freebox_data.fetch_snapshot()` on
a configurable interval, keeps an in-memory ring buffer (`collections.deque`)
sized to hold exactly 10 minutes of history, and a stdlib
`http.server.ThreadingHTTPServer` serves `templates/dashboard.html` plus two
JSON endpoints (`/api/snapshot`, `/api/history`) that the page polls with
`fetch()`. Charts are drawn with Chart.js (dual Y-axis for temperature/RPM).

**Tech Stack:** Python 3, `requests` (already used by the project),
`http.server` (stdlib, no new HTTP framework dependency), `unittest` +
`unittest.mock` (stdlib — `pytest` is not installed in this environment and
this is a small personal-tool repo, so stdlib testing avoids adding a
dependency), Chart.js via CDN (`cdnjs.cloudflare.com`) in the frontend.

**Spec:** `docs/superpowers/specs/2026-09-16-dashboard-web-design.md`

## Global Constraints

- Same Freebox app identity as `fbxstat.py`: `APP_ID = "fr.fbxstat.app"`,
  token file `~/.fbxstat_token.json` — no new authorization prompt on the
  Freebox screen.
- `fbxstat.py`'s behavior and terminal output do not change — only its auth
  code is replaced by an import from `freebox_api`.
- History window is fixed at 10 minutes regardless of `--interval`
  (`maxlen = round(600 / interval)`).
- No WebSocket, no persistence beyond process memory, no HTTPS, no dashboard
  login — local network tool, same trust model as the existing scripts.
- Base API URL: `http://mafreebox.freebox.fr/api/v8` (already used
  throughout the codebase).

---

## Task 1: Extract `freebox_api.py` and refactor `fbxstat.py`

**Files:**
- Create: `freebox_api.py`
- Create: `tests/test_freebox_api.py`
- Modify: `fbxstat.py:1-59` (remove duplicated auth code, import from
  `freebox_api` instead)

**Interfaces:**
- Produces (used by Task 2 and Task 3):
  - `freebox_api.BASE_URL: str`
  - `freebox_api.TOKEN_FILE: str`
  - `class freebox_api.AuthRequired(Exception)` — raised when the Freebox
    reports `error_code == "auth_required"` for a call that needs a fresh
    session
  - `freebox_api.get_app_token() -> str`
  - `freebox_api.open_session(app_token: str) -> str` (returns
    `session_token`)

- [ ] **Step 1: Create `freebox_api.py` with the auth code moved from `fbxstat.py`**

```python
#!/usr/bin/env python3
"""Authentification Freebox partagee entre fbxstat.py et fbxstat_web.py."""
import hashlib
import hmac
import json
import os
import time

import requests

APP_ID = "fr.fbxstat.app"
APP_NAME = "FbxStat"
APP_VERSION = "1.01"
DEVICE_NAME = "fbxstat"
TOKEN_FILE = os.path.expanduser("~/.fbxstat_token.json")
BASE_URL = "http://mafreebox.freebox.fr/api/v8"


class AuthRequired(Exception):
    """Levee quand la Freebox demande une nouvelle session (token expire)."""


def register_app():
    resp = requests.post(f"{BASE_URL}/login/authorize/", json={
        "app_id": APP_ID,
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "device_name": DEVICE_NAME,
    }).json()["result"]
    app_token, track_id = resp["app_token"], resp["track_id"]

    print("Valide la demande sur l'ecran de la Freebox...")
    while True:
        status = requests.get(f"{BASE_URL}/login/authorize/{track_id}").json()["result"]["status"]
        if status == "granted":
            break
        if status in ("denied", "timeout"):
            raise RuntimeError(f"Autorisation refusee ({status})")
        time.sleep(1)

    with open(TOKEN_FILE, "w") as f:
        json.dump({"app_token": app_token}, f)
    return app_token


def get_app_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return json.load(f)["app_token"]
    return register_app()


def open_session(app_token):
    challenge = requests.get(f"{BASE_URL}/login/").json()["result"]["challenge"]
    password = hmac.new(app_token.encode(), challenge.encode(), hashlib.sha1).hexdigest()
    resp = requests.post(f"{BASE_URL}/login/session/", json={
        "app_id": APP_ID,
        "password": password,
    }).json()
    if not resp["success"]:
        raise RuntimeError(f"Login echoue: {resp}")
    return resp["result"]["session_token"]
```

- [ ] **Step 2: Write the failing tests for `freebox_api.py`**

```python
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
    @patch("freebox_api.requests.post")
    @patch("freebox_api.requests.get")
    def test_success_returns_session_token(self, mock_get, mock_post):
        mock_get.return_value = MagicMock(json=lambda: {"result": {"challenge": "chal"}})
        mock_post.return_value = MagicMock(json=lambda: {
            "success": True,
            "result": {"session_token": "sess-token"},
        })

        token = freebox_api.open_session("app-token")

        self.assertEqual(token, "sess-token")
        mock_post.assert_called_once()

    @patch("freebox_api.requests.post")
    @patch("freebox_api.requests.get")
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
    @patch("freebox_api.requests.get")
    @patch("freebox_api.requests.post")
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
        mock_file().write.assert_called_once()

    @patch("freebox_api.time.sleep")
    @patch("freebox_api.requests.get")
    @patch("freebox_api.requests.post")
    def test_raises_on_timeout(self, mock_post, mock_get, mock_sleep):
        mock_post.return_value = MagicMock(json=lambda: {
            "result": {"app_token": "new-token", "track_id": 42},
        })
        mock_get.return_value = MagicMock(json=lambda: {"result": {"status": "timeout"}})

        with self.assertRaises(RuntimeError):
            freebox_api.register_app()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m unittest tests.test_freebox_api -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'freebox_api'`
(the file doesn't exist yet if you're doing this step before Step 1 — since
Step 1 already created the file above, running now should instead PASS; if
so, skip to Step 4 and just confirm all 5 tests pass)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest tests.test_freebox_api -v`
Expected: `OK` (5 tests passed)

- [ ] **Step 5: Refactor `fbxstat.py` to import from `freebox_api`**

Replace lines 1-59 of `fbxstat.py` (imports through `open_session`) with:

```python
#!/usr/bin/env python3
"""Affiche en temps reel la vitesse WAN (down/up) de la Freebox."""
import sys
import time

import requests

from freebox_api import BASE_URL, get_app_token, open_session
```

Leave everything from `fmt_bytes_per_sec` (previously line 62) onward
unchanged.

- [ ] **Step 6: Verify `fbxstat.py` still runs**

Run: `python -c "import ast; ast.parse(open('fbxstat.py').read())"`
Expected: no output (syntax is valid)

Run: `python -c "import fbxstat; print(fbxstat.get_app_token is __import__('freebox_api').get_app_token)"`
Expected: `True` (confirms `fbxstat.py` now uses the shared function instead
of a duplicate)

- [ ] **Step 7: Commit**

```bash
git add freebox_api.py tests/test_freebox_api.py fbxstat.py
git commit -m "Extract Freebox auth into shared freebox_api module"
```

---

## Task 2: `freebox_data.py` — data fetching for the dashboard

**Files:**
- Create: `freebox_data.py`
- Create: `tests/test_freebox_data.py`

**Interfaces:**
- Consumes: `freebox_api.BASE_URL`, `freebox_api.AuthRequired`
- Produces (used by Task 3):
  - `freebox_data.fetch_ports(session_token: str) -> list[dict]` — each dict:
    `{"port": str, "speed": str, "down_bps": float, "up_bps": float}`
  - `freebox_data.fetch_devices(session_token: str) -> list[dict]` — each
    dict: `{"name": str, "ipv4": str, "ipv6": str, "type": str, "vendor": str}`
  - `freebox_data.fetch_snapshot(session_token: str) -> dict` — keys:
    `model, firmware, internet_ok, auth_ok, phone_ok, ipv4, ipv6, uptime,
    wan_down_bps, wan_up_bps, sensors, fans, ports, devices`. Raises
    `freebox_api.AuthRequired` if the Freebox reports `auth_required` on the
    initial `/connection/` call.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_freebox_data.py
import unittest
from unittest.mock import patch, MagicMock

import freebox_data
from freebox_api import AuthRequired


def _resp(payload):
    return MagicMock(json=lambda: payload)


class TestFetchPorts(unittest.TestCase):
    @patch("freebox_data.requests.get")
    def test_skips_down_links_and_maps_speed(self, mock_get):
        def side_effect(url, headers=None):
            if url.endswith("/switch/status/"):
                return _resp({"success": True, "result": [
                    {"id": 1, "link": "up", "speed": "2500"},
                    {"id": 2, "link": "down", "speed": "1000"},
                    {"id": 9999, "link": "up", "speed": "10000"},
                ]})
            if url.endswith("/switch/port/1/stats/"):
                return _resp({"success": True, "result": {"rx_bytes_rate": 100, "tx_bytes_rate": 10}})
            if url.endswith("/switch/port/9999/stats/"):
                return _resp({"success": True, "result": {"rx_bytes_rate": -1, "tx_bytes_rate": -1}})
            raise AssertionError(f"unexpected URL {url}")

        mock_get.side_effect = side_effect

        ports = freebox_data.fetch_ports("session-token")

        self.assertEqual(ports, [
            {"port": "1", "speed": "2.5G", "down_bps": 800.0, "up_bps": 80.0},
            {"port": "SFP+", "speed": "10G", "down_bps": 0.0, "up_bps": 0.0},
        ])


class TestFetchDevices(unittest.TestCase):
    @patch("freebox_data.requests.get")
    def test_filters_and_sorts_by_ipv4(self, mock_get):
        mock_get.return_value = _resp({"result": [
            {
                "primary_name": "B",
                "host_type": "smartphone",
                "vendor_name": "Acme",
                "l3connectivities": [
                    {"af": "ipv4", "addr": "192.168.1.20", "active": True},
                    {"af": "ipv6", "addr": "fe80::2", "active": True},
                ],
            },
            {
                "primary_name": "A",
                "host_type": "workstation",
                "vendor_name": "Acme",
                "l3connectivities": [
                    {"af": "ipv4", "addr": "192.168.1.5", "active": True},
                ],
            },
            {
                "primary_name": "NoIP",
                "l3connectivities": [
                    {"af": "ipv4", "addr": "192.168.1.99", "active": False},
                ],
            },
        ]})

        devices = freebox_data.fetch_devices("session-token")

        self.assertEqual([d["name"] for d in devices], ["A", "B"])
        self.assertEqual(devices[0]["ipv4"], "192.168.1.5")
        self.assertEqual(devices[1]["ipv6"], "fe80::2")


class TestFetchSnapshot(unittest.TestCase):
    @patch("freebox_data.fetch_devices", return_value=[])
    @patch("freebox_data.fetch_ports", return_value=[])
    @patch("freebox_data.requests.get")
    def test_raises_auth_required(self, mock_get, mock_ports, mock_devices):
        mock_get.return_value = _resp({"success": False, "error_code": "auth_required"})

        with self.assertRaises(AuthRequired):
            freebox_data.fetch_snapshot("session-token")

    @patch("freebox_data.fetch_devices", return_value=[{"name": "A"}])
    @patch("freebox_data.fetch_ports", return_value=[{"port": "1"}])
    @patch("freebox_data.requests.get")
    def test_builds_full_snapshot(self, mock_get, mock_ports, mock_devices):
        def side_effect(url, headers=None):
            if url.endswith("/connection/"):
                return _resp({"success": True, "result": {
                    "state": "up", "ipv4": "1.2.3.4", "ipv6": "::1",
                    "rate_down": 100, "rate_up": 10,
                }})
            if url.endswith("/system/"):
                return _resp({"result": {
                    "model_info": {"pretty_name": "Freebox v9 (r1)"},
                    "firmware_version": "4.12.2",
                    "box_authenticated": True,
                    "uptime": "1 jour",
                    "sensors": [{"id": "temp_cpu0", "name": "CPU 0", "value": 55}],
                    "fans": [{"id": "fan0_speed", "name": "Ventilateur 1", "value": 800}],
                }})
            if url.endswith("/phone/"):
                return _resp({"result": [{"hardware_defect": False}]})
            raise AssertionError(f"unexpected URL {url}")

        mock_get.side_effect = side_effect

        snap = freebox_data.fetch_snapshot("session-token")

        self.assertEqual(snap["model"], "Freebox v9 (r1)")
        self.assertEqual(snap["firmware"], "4.12.2")
        self.assertTrue(snap["internet_ok"])
        self.assertTrue(snap["auth_ok"])
        self.assertTrue(snap["phone_ok"])
        self.assertEqual(snap["wan_down_bps"], 800.0)
        self.assertEqual(snap["wan_up_bps"], 80.0)
        self.assertEqual(snap["ports"], [{"port": "1"}])
        self.assertEqual(snap["devices"], [{"name": "A"}])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_freebox_data -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'freebox_data'`

- [ ] **Step 3: Write `freebox_data.py`**

```python
#!/usr/bin/env python3
"""Recuperation des donnees Freebox pour le dashboard web (retourne des dicts JSON-ready)."""
import requests

from freebox_api import BASE_URL, AuthRequired

SPEED_LABELS = {
    "10000": "10G",
    "2500": "2.5G",
    "1000": "1G",
    "100": "100M",
    "10": "10M",
}


def _get(path, session_token):
    return requests.get(f"{BASE_URL}{path}", headers={"X-Fbx-App-Auth": session_token}).json()


def fetch_ports(session_token):
    ports_resp = _get("/switch/status/", session_token)
    if not ports_resp.get("success"):
        return []

    rows = []
    for p in ports_resp["result"]:
        if p.get("link") != "up":
            continue
        stats_resp = _get(f"/switch/port/{p['id']}/stats/", session_token)
        if not stats_resp.get("success"):
            continue
        s = stats_resp["result"]
        name = "SFP+" if p["id"] == 9999 else str(p["id"])
        speed = SPEED_LABELS.get(p.get("speed"), str(p.get("speed", "N/A")))
        rows.append({
            "port": name,
            "speed": speed,
            "down_bps": max(s["rx_bytes_rate"], 0) * 8,
            "up_bps": max(s["tx_bytes_rate"], 0) * 8,
        })
    return rows


def fetch_devices(session_token):
    hosts = _get("/lan/browser/pub/", session_token)["result"]

    rows = []
    for h in hosts:
        ipv4 = next(
            (c["addr"] for c in h.get("l3connectivities", []) if c.get("af") == "ipv4" and c.get("active")),
            None,
        )
        if not ipv4:
            continue
        ipv6 = next(
            (c["addr"] for c in h.get("l3connectivities", []) if c.get("af") == "ipv6" and c["addr"].startswith("fe80")),
            "N/A",
        )
        rows.append({
            "name": h.get("primary_name", "?"),
            "ipv4": ipv4,
            "ipv6": ipv6,
            "type": h.get("host_type") or "N/A",
            "vendor": h.get("vendor_name") or "N/A",
        })

    rows.sort(key=lambda r: tuple(int(x) for x in r["ipv4"].split(".")))
    return rows


def fetch_snapshot(session_token):
    data = _get("/connection/", session_token)
    if not data.get("success") and data.get("error_code") == "auth_required":
        raise AuthRequired()
    result = data["result"]

    sys_data = _get("/system/", session_token)["result"]

    try:
        phone_data = _get("/phone/", session_token)["result"]
        phone_ok = all(not p.get("hardware_defect", True) for p in phone_data)
    except Exception:
        phone_ok = False

    return {
        "model": sys_data.get("model_info", {}).get("pretty_name", "N/A"),
        "firmware": sys_data.get("firmware_version", "N/A"),
        "internet_ok": result.get("state") == "up",
        "auth_ok": sys_data.get("box_authenticated", False),
        "phone_ok": phone_ok,
        "ipv4": result.get("ipv4", "N/A"),
        "ipv6": result.get("ipv6", "N/A"),
        "uptime": sys_data.get("uptime", "N/A"),
        "wan_down_bps": max(result.get("rate_down", 0), 0) * 8,
        "wan_up_bps": max(result.get("rate_up", 0), 0) * 8,
        "sensors": sys_data.get("sensors", []),
        "fans": sys_data.get("fans", []),
        "ports": fetch_ports(session_token),
        "devices": fetch_devices(session_token),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest tests.test_freebox_data -v`
Expected: `OK` (4 tests passed)

- [ ] **Step 5: Commit**

```bash
git add freebox_data.py tests/test_freebox_data.py
git commit -m "Add freebox_data module for dashboard JSON data"
```

---

## Task 3: `fbxstat_web.py` — history buffer, collector thread, HTTP server

**Files:**
- Create: `fbxstat_web.py`
- Create: `tests/test_fbxstat_web.py`
- Create: `templates/dashboard.html` (minimal placeholder page for this
  task's tests only — full content is written in Task 4)

**Interfaces:**
- Consumes: `freebox_api.get_app_token`, `freebox_api.open_session`,
  `freebox_api.AuthRequired`, `freebox_data.fetch_snapshot`
- Produces (used by Task 4 / manual verification):
  - `fbxstat_web.History(maxlen: int)` with `.add(point: dict)` and
    `.to_list() -> list[dict]`
  - `fbxstat_web.build_arg_parser() -> argparse.ArgumentParser` (has
    `--interval`, default `1.0`)
  - `fbxstat_web.Handler` (`http.server.BaseHTTPRequestHandler` subclass)
    serving `GET /`, `GET /api/snapshot`, `GET /api/history`
  - Module globals `fbxstat_web.STATE` (`{"snapshot": dict|None,
    "history": History}`), `fbxstat_web.STATE_LOCK` (`threading.Lock`),
    `fbxstat_web.INTERVAL` (`float`), `fbxstat_web.DASHBOARD_HTML_PATH`
    (`str`) — tests set these directly before exercising `Handler`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fbxstat_web.py
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_fbxstat_web -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fbxstat_web'`

- [ ] **Step 3: Create a placeholder `templates/dashboard.html`**

```html
<!doctype html>
<html><body>placeholder</body></html>
```

(This gets overwritten with the real page in Task 4; it only needs to exist
so `fbxstat_web.py` has a real default file to point at during manual runs.)

- [ ] **Step 4: Write `fbxstat_web.py`**

```python
#!/usr/bin/env python3
"""Serveur web du dashboard Freebox (vitesse WAN, temperatures, ports, appareils)."""
import argparse
import collections
import http.server
import json
import os
import sys
import threading
import time

from freebox_api import AuthRequired, get_app_token, open_session
from freebox_data import fetch_snapshot

PORT = 8000
DASHBOARD_HTML_PATH = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")

INTERVAL = 1.0
STATE_LOCK = threading.Lock()
STATE = {"snapshot": None, "history": None}


class History:
    def __init__(self, maxlen):
        self._data = collections.deque(maxlen=maxlen)

    def add(self, point):
        self._data.append(point)

    def to_list(self):
        return list(self._data)


def collect_loop(app_token):
    session_token = open_session(app_token)
    while True:
        try:
            snapshot = fetch_snapshot(session_token)
        except AuthRequired:
            session_token = open_session(app_token)
            continue
        except Exception as e:
            print(f"collecte: erreur ignoree: {e}", file=sys.stderr)
            time.sleep(INTERVAL)
            continue

        point = {
            "t": time.time(),
            "wan_down_bps": snapshot["wan_down_bps"],
            "wan_up_bps": snapshot["wan_up_bps"],
            "sensors": snapshot["sensors"],
            "fans": snapshot["fans"],
        }
        with STATE_LOCK:
            STATE["snapshot"] = snapshot
            STATE["history"].add(point)
        time.sleep(INTERVAL)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self._serve_file(DASHBOARD_HTML_PATH, "text/html")
        elif self.path == "/api/snapshot":
            self._serve_json(self._snapshot_payload())
        elif self.path == "/api/history":
            with STATE_LOCK:
                points = STATE["history"].to_list()
            self._serve_json({"points": points})
        else:
            self.send_response(404)
            self.end_headers()

    def _snapshot_payload(self):
        with STATE_LOCK:
            snapshot = STATE["snapshot"]
        if snapshot is None:
            return {"interval": INTERVAL}
        return {**snapshot, "interval": INTERVAL}

    def _serve_file(self, path, content_type):
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Dashboard web Freebox")
    parser.add_argument("--interval", type=float, default=1.0, help="secondes entre deux rafraichissements")
    return parser


def main():
    global INTERVAL
    args = build_arg_parser().parse_args()
    INTERVAL = args.interval
    STATE["history"] = History(maxlen=max(1, round(600 / INTERVAL)))

    app_token = get_app_token()
    threading.Thread(target=collect_loop, args=(app_token,), daemon=True).start()

    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Dashboard sur http://localhost:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m unittest tests.test_fbxstat_web -v`
Expected: `OK` (7 tests passed)

- [ ] **Step 6: Commit**

```bash
git add fbxstat_web.py tests/test_fbxstat_web.py templates/dashboard.html
git commit -m "Add fbxstat_web server: history buffer, collector thread, HTTP API"
```

---

## Task 4: `templates/dashboard.html` — the actual page

**Files:**
- Modify: `templates/dashboard.html` (replace the Task 3 placeholder with
  the real page)
- Create: `tests/test_dashboard_html.py`

**Interfaces:**
- Consumes: `GET /api/snapshot` and `GET /api/history` as shaped by Task 2/3
  (`wan_down_bps`, `wan_up_bps`, `sensors: [{id,name,value}]`,
  `fans: [{id,name,value}]`, `ports: [{port,speed,down_bps,up_bps}]`,
  `devices: [{name,ipv4,ipv6,type,vendor}]`, `interval`, plus
  `model, firmware, internet_ok, auth_ok, phone_ok, ipv4, ipv6, uptime`)
- Produces: nothing consumed by later tasks (this is the last task)

- [ ] **Step 1: Write the failing test (content smoke test)**

A full browser test is out of scope for this stdlib-only project (no
headless browser tooling installed) — this test checks the static markers
the JS/CSS depend on are present, so a future edit can't silently delete
the chart canvases or table bodies the JS looks up by id.

```python
# tests/test_dashboard_html.py
import unittest

with open("templates/dashboard.html", encoding="utf-8") as f:
    HTML = f.read()


class TestDashboardHtml(unittest.TestCase):
    def test_has_chartjs_cdn_script(self):
        self.assertIn("cdnjs.cloudflare.com/ajax/libs/Chart.js", HTML)

    def test_has_both_chart_canvases(self):
        self.assertIn('id="wan-chart"', HTML)
        self.assertIn('id="temp-chart"', HTML)

    def test_has_both_table_bodies(self):
        self.assertIn('id="ports-body"', HTML)
        self.assertIn('id="devices-body"', HTML)

    def test_has_status_header_elements(self):
        for el_id in ["hdr-model", "hdr-firmware", "hdr-internet", "hdr-auth", "hdr-phone", "hdr-ipv4", "hdr-ipv6", "hdr-uptime"]:
            self.assertIn(f'id="{el_id}"', HTML)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_dashboard_html -v`
Expected: FAIL (placeholder page from Task 3 has none of these markers)

- [ ] **Step 3: Write the real `templates/dashboard.html`**

```html
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Freebox Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.4/chart.umd.min.js"></script>
<style>
  :root { color-scheme: dark; }
  body { background: #0f1115; color: #e6e6e6; font-family: system-ui, sans-serif; margin: 0; padding: 16px; }
  .card { background: #1a1d24; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
  .header { display: flex; flex-wrap: wrap; gap: 16px; align-items: center; }
  .header span { white-space: nowrap; }
  .ok { color: #2ecc71; font-weight: bold; }
  .ko { color: #e74c3c; font-weight: bold; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #2a2e37; }
  canvas { max-height: 260px; }
  .charts { display: flex; flex-wrap: wrap; gap: 16px; }
  .charts .card { flex: 1 1 400px; }
</style>
</head>
<body>

<div class="card header">
  <span>Materiel: <b id="hdr-model">-</b></span>
  <span>FreeboxOS: <b id="hdr-firmware">-</b></span>
  <span>Internet: <b id="hdr-internet">-</b></span>
  <span>Authentification: <b id="hdr-auth">-</b></span>
  <span>Telephone: <b id="hdr-phone">-</b></span>
  <span>IPv4: <b id="hdr-ipv4">-</b></span>
  <span>IPv6: <b id="hdr-ipv6">-</b></span>
  <span>Uptime: <b id="hdr-uptime">-</b></span>
</div>

<div class="charts">
  <div class="card"><canvas id="wan-chart"></canvas></div>
  <div class="card"><canvas id="temp-chart"></canvas></div>
</div>

<div class="card">
  <h3>Ports</h3>
  <table>
    <thead><tr><th>Port</th><th>Vitesse</th><th>Down</th><th>Up</th></tr></thead>
    <tbody id="ports-body"></tbody>
  </table>
</div>

<div class="card">
  <h3>Appareils</h3>
  <table>
    <thead><tr><th>Nom</th><th>IPv4</th><th>IPv6 locale</th><th>Type</th><th>Constructeur</th></tr></thead>
    <tbody id="devices-body"></tbody>
  </table>
</div>

<script>
function fmtMbps(bps) { return (bps / 1_000_000).toFixed(2) + " Mb/s"; }
function fmtStatus(ok) { return ok ? '<span class="ok">OK</span>' : '<span class="ko">KO</span>'; }

const wanChart = new Chart(document.getElementById("wan-chart"), {
  type: "line",
  data: { labels: [], datasets: [
    { label: "Down (Mb/s)", data: [], borderColor: "#3498db", tension: 0.2 },
    { label: "Up (Mb/s)", data: [], borderColor: "#e67e22", tension: 0.2 },
  ]},
  options: { animation: false, responsive: true, scales: { y: { beginAtZero: true } } },
});

const tempChart = new Chart(document.getElementById("temp-chart"), {
  type: "line",
  data: { labels: [], datasets: [] },
  options: {
    animation: false, responsive: true,
    scales: {
      y: { type: "linear", position: "left", title: { display: true, text: "°C" } },
      y1: { type: "linear", position: "right", title: { display: true, text: "RPM" }, grid: { drawOnChartArea: false } },
    },
  },
});

const TEMP_COLORS = ["#e74c3c", "#f1c40f", "#2ecc71", "#9b59b6", "#1abc9c"];
const FAN_COLORS = ["#3498db", "#e67e22"];

function updateHeader(snap) {
  document.getElementById("hdr-model").textContent = snap.model ?? "-";
  document.getElementById("hdr-firmware").textContent = snap.firmware ?? "-";
  document.getElementById("hdr-internet").innerHTML = fmtStatus(snap.internet_ok);
  document.getElementById("hdr-auth").innerHTML = fmtStatus(snap.auth_ok);
  document.getElementById("hdr-phone").innerHTML = fmtStatus(snap.phone_ok);
  document.getElementById("hdr-ipv4").textContent = snap.ipv4 ?? "-";
  document.getElementById("hdr-ipv6").textContent = snap.ipv6 ?? "-";
  document.getElementById("hdr-uptime").textContent = snap.uptime ?? "-";
}

function updateTables(snap) {
  const portsBody = document.getElementById("ports-body");
  portsBody.innerHTML = (snap.ports ?? []).map(p =>
    `<tr><td>${p.port}</td><td>${p.speed}</td><td>${fmtMbps(p.down_bps)}</td><td>${fmtMbps(p.up_bps)}</td></tr>`
  ).join("");

  const devicesBody = document.getElementById("devices-body");
  devicesBody.innerHTML = (snap.devices ?? []).map(d =>
    `<tr><td>${d.name}</td><td>${d.ipv4}</td><td>${d.ipv6}</td><td>${d.type}</td><td>${d.vendor}</td></tr>`
  ).join("");
}

function updateCharts(points) {
  const labels = points.map(p => new Date(p.t * 1000).toLocaleTimeString());

  wanChart.data.labels = labels;
  wanChart.data.datasets[0].data = points.map(p => p.wan_down_bps / 1_000_000);
  wanChart.data.datasets[1].data = points.map(p => p.wan_up_bps / 1_000_000);
  wanChart.update();

  const sensorIds = [...new Map(points.flatMap(p => p.sensors ?? []).map(s => [s.id, s.name])).entries()];
  const fanIds = [...new Map(points.flatMap(p => p.fans ?? []).map(f => [f.id, f.name])).entries()];

  tempChart.data.labels = labels;
  tempChart.data.datasets = [
    ...sensorIds.map(([id, name], i) => ({
      label: name, yAxisID: "y", borderColor: TEMP_COLORS[i % TEMP_COLORS.length], tension: 0.2,
      data: points.map(p => (p.sensors ?? []).find(s => s.id === id)?.value ?? null),
    })),
    ...fanIds.map(([id, name], i) => ({
      label: name, yAxisID: "y1", borderColor: FAN_COLORS[i % FAN_COLORS.length], borderDash: [4, 2], tension: 0.2,
      data: points.map(p => (p.fans ?? []).find(f => f.id === id)?.value ?? null),
    })),
  ];
  tempChart.update();
}

let pollMs = 1000;

async function tick() {
  try {
    const snap = await fetch("/api/snapshot").then(r => r.json());
    pollMs = (snap.interval ?? 1) * 1000;
    updateHeader(snap);
    updateTables(snap);

    const hist = await fetch("/api/history").then(r => r.json());
    updateCharts(hist.points);
  } catch (e) {
    console.error(e);
  }
  setTimeout(tick, pollMs);
}

tick();
</script>
</body>
</html>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m unittest tests.test_dashboard_html -v`
Expected: `OK` (4 tests passed)

- [ ] **Step 5: Run the full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: `OK` (all tests across Tasks 1-4 pass)

- [ ] **Step 6: Manual verification (UI — not automated)**

Run: `python fbxstat_web.py`
Then open `http://localhost:8000` in a browser and confirm:
- Header shows model/firmware/IPv4/IPv6/uptime and OK/KO badges in
  green/red.
- The WAN chart starts drawing a line within a few seconds and keeps
  scrolling as time passes.
- The temperature/fan chart shows temperature lines against the left axis
  and a dashed fan-speed line against the right axis.
- Ports table lists only ports with an active link, with correct speed
  labels (10M/100M/1G/2.5G/10G).
- Devices table lists only devices with an IPv4, sorted by IPv4.

Run with a different interval to confirm the option works:
`python fbxstat_web.py --interval 5`

- [ ] **Step 7: Commit**

```bash
git add templates/dashboard.html tests/test_dashboard_html.py
git commit -m "Add dashboard frontend: charts and tables"
```
