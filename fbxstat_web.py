#!/usr/bin/env python3
"""Serveur web du dashboard Freebox (vitesse WAN, temperatures, ports, appareils)."""
import argparse
import collections
import http.server
import ipaddress
import json
import os
import socket
import sys
import threading
import time
import webbrowser

from freebox_api import AuthRequired, get_app_token, open_session
from freebox_data import fetch_snapshot

DASHBOARD_HTML_PATH = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")

INTERVAL = 3.0
IF_INET6_PATH = "/proc/net/if_inet6"
ALLOWED_INTERVALS = (1, 3, 5)
STATE_LOCK = threading.Lock()
STATE = {"snapshot": None, "snapshot_t": None, "history": None}


class History:
    def __init__(self, maxlen):
        self._data = collections.deque(maxlen=maxlen)

    def add(self, point):
        self._data.append(point)

    def to_list(self):
        return list(self._data)


def collect_loop(app_token):
    session_token = None
    while True:
        if session_token is None:
            try:
                session_token = open_session(app_token)
            except Exception as e:
                print(f"collecte: erreur ignoree: {e}", file=sys.stderr)
                time.sleep(INTERVAL)
                continue

        try:
            snapshot = fetch_snapshot(session_token)
        except AuthRequired:
            try:
                session_token = open_session(app_token)
            except Exception as e:
                print(f"collecte: erreur ignoree: {e}", file=sys.stderr)
                session_token = None
                time.sleep(INTERVAL)
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
            STATE["snapshot_t"] = point["t"]
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

    def do_POST(self):
        global INTERVAL
        if self.path != "/api/interval":
            self.send_error(404)
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.send_error(415)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 100:
                raise ValueError
            value = json.loads(self.rfile.read(length))["interval"]
            if isinstance(value, bool) or value not in ALLOWED_INTERVALS:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            self.send_error(400)
            return
        INTERVAL = float(value)
        self._serve_json({"interval": INTERVAL})

    def _snapshot_payload(self):
        with STATE_LOCK:
            snapshot = STATE["snapshot"]
            snapshot_t = STATE.get("snapshot_t")
        if snapshot is None:
            return {"interval": INTERVAL}
        return {**snapshot, "interval": INTERVAL, "t": snapshot_t}

    def _serve_file(self, path, content_type):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(500)
            return
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
    parser.add_argument("--interval", type=float, default=3.0, help="secondes entre deux rafraichissements")
    parser.add_argument("--host", default="127.0.0.1", help="adresse d'ecoute (0.0.0.0 pour tout le LAN)")
    parser.add_argument("--port", type=int, default=8000, help="port d'ecoute")
    parser.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur au demarrage")
    return parser


def http_url(host, port):
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


def browser_url(host, port):
    return http_url("127.0.0.1" if host in ("0.0.0.0", "::") else host, port)


def primary_ipv4():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET address: picks the default route, sends nothing
            return s.getsockname()[0]
    except OSError:
        return None


def link_local_ipv6():
    found = set()
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6)
        found.update(i[4][0].split("%")[0] for i in infos if i[4][0].startswith("fe80"))
    except OSError:
        pass
    # Linux: getaddrinfo(hostname) only reflects /etc/hosts, the kernel table lists the real interfaces
    try:
        with open(IF_INET6_PATH) as f:
            for line in f:
                raw = line.split()[0]
                if raw.startswith("fe80"):
                    found.add(ipaddress.IPv6Address(int(raw, 16)).compressed)
    except (OSError, IndexError):
        pass
    return sorted(found)


def lan_urls(host, port):
    if host not in ("0.0.0.0", "::"):
        return []
    urls = []
    ipv4 = primary_ipv4()
    if ipv4:
        urls.append(("IPv4", http_url(ipv4, port)))
    if host == "::":
        urls += [("IPv6", http_url(a, port)) for a in link_local_ipv6()]
    return urls


def make_server(host, port):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET

    class Server(http.server.ThreadingHTTPServer):
        address_family = family

        def server_bind(self):
            if family == socket.AF_INET6:
                # "::" then accepts IPv4 clients too (dual-stack)
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            super().server_bind()

    return Server((host, port), Handler)


def main():
    global INTERVAL
    parser = build_arg_parser()
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be positive")
    INTERVAL = args.interval
    STATE["history"] = History(maxlen=max(1, round(600 / min(INTERVAL, 1.0))))

    app_token = get_app_token()
    threading.Thread(target=collect_loop, args=(app_token,), daemon=True).start()

    server = make_server(args.host, args.port)
    print(f"Dashboard sur {http_url(args.host, args.port)}")
    for label, url in lan_urls(args.host, args.port):
        print(f"  {label} : {url}")
    if not args.no_browser:
        webbrowser.open(browser_url(args.host, args.port))
    server.serve_forever()


if __name__ == "__main__":
    main()
