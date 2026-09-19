#!/usr/bin/env python3
"""Recuperation des donnees Freebox pour le dashboard web (retourne des dicts JSON-ready)."""
import ipaddress

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
    return requests.get(f"{BASE_URL}{path}", headers={"X-Fbx-App-Auth": session_token}, timeout=10).json()


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
        # rx/tx are counted by the box: what it sends (tx) is the connected device's download
        rows.append({
            "port": name,
            "speed": speed,
            "down_bps": max(s["tx_bytes_rate"], 0) * 8,
            "up_bps": max(s["rx_bytes_rate"], 0) * 8,
            "down_bytes": max(s.get("tx_bytes", 0), 0),
            "up_bytes": max(s.get("rx_good_bytes", 0), 0),
        })
    return rows


def _last_used_ipv6(connectivities, keep):
    candidates = [c for c in connectivities if c.get("af") == "ipv6" and keep(c["addr"])]
    if not candidates:
        return "N/A"
    # max() keeps the first of equal keys, so without last_activity we fall back to the first listed
    return max(candidates, key=lambda c: c.get("last_activity", 0))["addr"]


def fetch_devices(session_token):
    hosts = _get("/lan/browser/pub/", session_token)["result"]

    rows = []
    for h in hosts:
        conns = h.get("l3connectivities", [])
        ipv4 = next((c["addr"] for c in conns if c.get("af") == "ipv4" and c.get("active")), None)
        if not ipv4:
            continue
        rows.append({
            "name": h.get("primary_name", "?"),
            "ipv4": ipv4,
            "ipv6": _last_used_ipv6(conns, lambda a: a.startswith("fe80")),
            "ipv6_global": _last_used_ipv6(conns, lambda a: ipaddress.ip_address(a).is_global),
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
        "wan_down_bytes": max(result.get("bytes_down", 0), 0),
        "wan_up_bytes": max(result.get("bytes_up", 0), 0),
        "sensors": sys_data.get("sensors", []),
        "fans": sys_data.get("fans", []),
        "ports": fetch_ports(session_token),
        "devices": fetch_devices(session_token),
    }
