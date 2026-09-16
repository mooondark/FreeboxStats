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

DASHBOARD_HTML_PATH = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")

INTERVAL = 1.0
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
    parser.add_argument("--interval", type=float, default=1.0, help="secondes entre deux rafraichissements")
    parser.add_argument("--host", default="127.0.0.1", help="adresse d'ecoute (0.0.0.0 pour tout le LAN)")
    parser.add_argument("--port", type=int, default=8000, help="port d'ecoute")
    return parser


def main():
    global INTERVAL
    parser = build_arg_parser()
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be positive")
    INTERVAL = args.interval
    STATE["history"] = History(maxlen=max(1, round(600 / INTERVAL)))

    app_token = get_app_token()
    threading.Thread(target=collect_loop, args=(app_token,), daemon=True).start()

    server = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Dashboard sur http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
