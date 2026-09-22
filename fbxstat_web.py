#!/usr/bin/env python3
"""Serveur web du dashboard Freebox (vitesse WAN, temperatures, ports, appareils)."""
import argparse
import collections
import http.server
import ipaddress
import json
import os
import socket
import ssl
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
IDLE_AFTER = 15.0
TLS_HANDSHAKE_TIMEOUT = 10.0
LAST_ACTIVITY = 0.0
ACTIVITY = threading.Event()
STATE_LOCK = threading.Lock()
STATE = {"snapshot": None, "snapshot_t": None, "history": None}


class History:
    def __init__(self, maxlen):
        self._data = collections.deque(maxlen=maxlen)

    def add(self, point):
        self._data.append(point)

    def to_list(self):
        return list(self._data)

    def clear(self):
        self._data.clear()


def touch():
    global LAST_ACTIVITY
    LAST_ACTIVITY = time.time()
    ACTIVITY.set()


def wait_for_viewer():
    """Block while nobody has asked for data for IDLE_AFTER seconds (no polling of the Freebox)."""
    while time.time() - LAST_ACTIVITY > IDLE_AFTER:
        with STATE_LOCK:
            STATE["snapshot"] = None
            STATE["snapshot_t"] = None
            STATE["history"].clear()
        ACTIVITY.clear()
        if time.time() - LAST_ACTIVITY <= IDLE_AFTER:  # a request slipped in before the clear
            break
        ACTIVITY.wait()


def collect_loop(app_token):
    session_token = None
    while True:
        wait_for_viewer()
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
        if self.path in ("/", "/api/snapshot", "/api/history"):
            touch()
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
    parser.add_argument(
        "--https", action="store_true",
        help="servir en HTTPS avec un certificat auto-signe (necessaire pour l'ecran toujours allume sur mobile)",
    )
    return parser


def http_url(host, port, scheme="http"):
    return f"{scheme}://[{host}]:{port}" if ":" in host else f"{scheme}://{host}:{port}"


def browser_url(host, port, scheme="http"):
    return http_url("127.0.0.1" if host in ("0.0.0.0", "::") else host, port, scheme)


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


def lan_urls(host, port, scheme="http"):
    if host not in ("0.0.0.0", "::"):
        return []
    urls = []
    ipv4 = primary_ipv4()
    if ipv4:
        urls.append(("IPv4", http_url(ipv4, port, scheme)))
    if host == "::":
        urls += [("IPv6", http_url(a, port, scheme)) for a in link_local_ipv6()]
    return urls


def build_sans():
    sans = ["127.0.0.1", "::1", "localhost"]
    ipv4 = primary_ipv4()
    if ipv4:
        sans.append(ipv4)
    sans += link_local_ipv6()
    return sans


def cert_paths():
    cert_dir = os.path.expanduser("~/.fbxstat_cert")
    return (
        os.path.join(cert_dir, "cert.pem"),
        os.path.join(cert_dir, "key.pem"),
        os.path.join(cert_dir, "meta.json"),
    )


def ensure_self_signed_cert(sans):
    """Reuse the cached self-signed cert if it already covers `sans`, else (re)generate it."""
    cert_path, key_path, meta_path = cert_paths()
    if all(os.path.exists(p) for p in (cert_path, key_path, meta_path)):
        try:
            with open(meta_path) as f:
                if json.load(f).get("sans") == sans:
                    return cert_path, key_path
        except (OSError, ValueError):
            pass

    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "FbxStat")])
    alt_names = []
    for s in sans:
        try:
            alt_names.append(x509.IPAddress(ipaddress.ip_address(s)))
        except ValueError:
            alt_names.append(x509.DNSName(s))
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .sign(key, hashes.SHA256())
    )

    os.makedirs(os.path.dirname(cert_path), exist_ok=True)
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    os.chmod(key_path, 0o600)
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(meta_path, "w") as f:
        json.dump({"sans": sans}, f)

    return cert_path, key_path


def make_server(host, port, ssl_context=None):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET

    class Server(http.server.ThreadingHTTPServer):
        address_family = family

        def server_bind(self):
            if family == socket.AF_INET6:
                # "::" then accepts IPv4 clients too (dual-stack)
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            super().server_bind()

        def finish_request(self, request, client_address):
            # The handshake blocks, so do it here (already in the per-request thread)
            # rather than in get_request(), which runs in the single-threaded accept
            # loop and would freeze every other client while one handshake is stuck.
            if ssl_context is not None:
                # A connection that never completes the handshake (dropped packets,
                # a mobile client's speculative/retried connections) must not pin
                # this thread forever: piling those up eventually starves the
                # server of threads/file descriptors for every client, including
                # localhost.
                request.settimeout(TLS_HANDSHAKE_TIMEOUT)
                try:
                    request = ssl_context.wrap_socket(request, server_side=True)
                except (ssl.SSLError, OSError):
                    request.close()
                    return
                request.settimeout(None)  # back to normal blocking I/O for the request itself
            super().finish_request(request, client_address)

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

    ssl_context = None
    scheme = "http"
    if args.https:
        try:
            cert_path, key_path = ensure_self_signed_cert(build_sans())
        except ImportError:
            parser.error("--https necessite le paquet 'cryptography' (pip install cryptography)")
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(cert_path, key_path)
        scheme = "https"

    server = make_server(args.host, args.port, ssl_context=ssl_context)
    print(f"Dashboard sur {http_url(args.host, args.port, scheme)}")
    for label, url in lan_urls(args.host, args.port, scheme):
        print(f"  {label} : {url}")
    if args.https:
        print("  Certificat auto-signe : le navigateur affichera un avertissement a accepter.")
    if not args.no_browser:
        webbrowser.open(browser_url(args.host, args.port, scheme))
    server.serve_forever()


if __name__ == "__main__":
    main()
