#!/usr/bin/env python3
"""Affiche en temps reel la vitesse WAN (down/up) de la Freebox."""
import hashlib
import hmac
import json
import os
import sys
import time

import requests

APP_ID = "fr.fbxstat.app"
APP_NAME = "FbxStat"
APP_VERSION = "1.01"
DEVICE_NAME = "fbxstat"
TOKEN_FILE = os.path.expanduser("~/.fbxstat_token.json")
BASE_URL = "http://mafreebox.freebox.fr/api/v8"


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


def fmt_bytes_per_sec(bytes_per_sec):
    mb = max(bytes_per_sec, 0) * 8 / 1_000_000
    return f"{mb:6.2f} Mb/s"


def fmt_status(ok):
    return "\x1b[32mOK\x1b[0m" if ok else "\x1b[31mKO\x1b[0m"


def get_devices_table(session_token):
    hosts = requests.get(f"{BASE_URL}/lan/browser/pub/", headers={"X-Fbx-App-Auth": session_token}).json()["result"]

    rows = []
    for h in hosts:
        ipv4 = next((c["addr"] for c in h.get("l3connectivities", []) if c.get("af") == "ipv4" and c.get("active")), "N/A")
        ipv6 = next(
            (c["addr"] for c in h.get("l3connectivities", []) if c.get("af") == "ipv6" and c["addr"].startswith("fe80")),
            "N/A",
        )
        if ipv4 == "N/A":
            continue
        vendor = h.get("vendor_name") or "N/A"
        host_type = h.get("host_type") or "N/A"
        rows.append((h.get("primary_name", "?"), ipv4, ipv6, host_type, vendor))

    rows.sort(key=lambda r: tuple(int(x) for x in r[1].split(".")))

    header = ("Nom", "IPv4", "IPv6 locale", "Type", "Constructeur")
    widths = [max(len(r[i]) for r in rows + [header]) for i in range(5)]
    lines = ["| " + " | ".join(h.ljust(w) for h, w in zip(header, widths)) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(c.ljust(w) for c, w in zip(r, widths)) + " |")
    return lines


SPEED_LABELS = {
    "10000": "10G",
    "2500": "2.5G",
    "1000": "1G",
    "100": "100M",
    "10": "10M",
}


def get_ports_table(session_token):
    ports = requests.get(f"{BASE_URL}/switch/status/", headers={"X-Fbx-App-Auth": session_token}).json()
    if not ports.get("success"):
        return []

    rows = []
    for p in ports["result"]:
        if p.get("link") != "up":
            continue
        stats = requests.get(f"{BASE_URL}/switch/port/{p['id']}/stats/", headers={"X-Fbx-App-Auth": session_token}).json()
        if not stats.get("success"):
            continue
        s = stats["result"]
        name = "SFP+" if p["id"] == 9999 else str(p["id"])
        speed = SPEED_LABELS.get(p.get("speed"), str(p.get("speed", "N/A")))
        rows.append((name, speed, fmt_bytes_per_sec(s["rx_bytes_rate"]), fmt_bytes_per_sec(s["tx_bytes_rate"])))

    header = ("Port", "Vitesse", "↓", "↑")
    widths = [max(len(r[i]) for r in rows + [header]) for i in range(4)]
    lines = ["| " + " | ".join(h.ljust(w) for h, w in zip(header, widths)) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(c.ljust(w) for c, w in zip(r, widths)) + " |")
    return lines


def main():
    app_token = get_app_token()
    session_token = open_session(app_token)

    n_lines = 0
    while True:
        r = requests.get(f"{BASE_URL}/connection/", headers={"X-Fbx-App-Auth": session_token})
        data = r.json()

        if not data.get("success") and data.get("error_code") == "auth_required":
            session_token = open_session(app_token)
            continue

        result = data["result"]
        down = fmt_bytes_per_sec(result["rate_down"])
        up = fmt_bytes_per_sec(result["rate_up"])

        sys_data = requests.get(f"{BASE_URL}/system/", headers={"X-Fbx-App-Auth": session_token}).json()["result"]
        phone_data = requests.get(f"{BASE_URL}/phone/", headers={"X-Fbx-App-Auth": session_token}).json()["result"]

        internet_ok = result.get("state") == "up"
        auth_ok = sys_data.get("box_authenticated", False)
        phone_ok = all(not p.get("hardware_defect", True) for p in phone_data)
        model = sys_data.get("model_info", {}).get("pretty_name", "N/A")
        firmware = sys_data.get("firmware_version", "N/A")

        lines = [
            f"Materiel: {model}  |  FreeboxOS: {firmware}  |  "
            f"Internet: {fmt_status(internet_ok)}  Authentification: {fmt_status(auth_ok)}  Telephone: {fmt_status(phone_ok)}"
        ]
        lines += [f"IPv4: {result.get('ipv4', 'N/A')}", f"IPv6: {result.get('ipv6', 'N/A')}"]
        lines += [f"Uptime: {sys_data.get('uptime', 'N/A')}"]
        lines += [f"↓ {down}", f"↑ {up}"]
        lines += [f"{s['name']}: {s['value']}°C" for s in sys_data.get("sensors", [])]
        lines += [f"{f['name']}: {f['value']} rpm" for f in sys_data.get("fans", [])]
        lines += get_ports_table(session_token)
        lines += get_devices_table(session_token)

        if n_lines:
            sys.stdout.write(f"\x1b[{n_lines}A")
        for line in lines:
            sys.stdout.write("\x1b[2K" + line + "\n")
        sys.stdout.flush()
        n_lines = len(lines)
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
