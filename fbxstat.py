#!/usr/bin/env python3
"""Affiche en temps reel la vitesse WAN (down/up) de la Freebox."""
import sys
import time

import requests

from freebox_api import BASE_URL, get_app_token, open_session
from freebox_data import fetch_devices, fetch_ports


def fmt_bps(bps):
    return f"{max(bps, 0) / 1_000_000:6.2f} Mb/s"


def fmt_bytes_per_sec(bytes_per_sec):
    return fmt_bps(bytes_per_sec * 8)


def fmt_size(n):
    n = max(n, 0)
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1000:
            return f"{n:.0f} o" if unit == "o" else f"{n:.2f} {unit}"
        n /= 1000
    return f"{n:.2f} To"


def fmt_status(ok):
    return "\x1b[32mOK\x1b[0m" if ok else "\x1b[31mKO\x1b[0m"


def get_devices_table(session_token):
    rows = [
        (d["name"], d["ipv4"], d["ipv6"], d["ipv6_global"], d["type"], d["vendor"])
        for d in fetch_devices(session_token)
    ]

    header = ("Nom", "IPv4", "IPv6 locale", "IPv6 globale", "Type", "Constructeur")
    widths = [max(len(r[i]) for r in rows + [header]) for i in range(6)]
    lines = ["| " + " | ".join(h.ljust(w) for h, w in zip(header, widths)) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(c.ljust(w) for c, w in zip(r, widths)) + " |")
    return lines


def get_ports_table(session_token):
    rows = [
        (p["port"], p["speed"], fmt_bps(p["down_bps"]), fmt_bps(p["up_bps"]),
         fmt_size(p["down_bytes"]), fmt_size(p["up_bytes"]))
        for p in fetch_ports(session_token)
    ]

    header = ("Port", "Vitesse", "↓", "↑", "Total ↓", "Total ↑")
    widths = [max(len(r[i]) for r in rows + [header]) for i in range(6)]
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
        lines += [f"Total: ↓ {fmt_size(result.get('bytes_down', 0))}  ↑ {fmt_size(result.get('bytes_up', 0))}"]
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
