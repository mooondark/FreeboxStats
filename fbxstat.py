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
APP_VERSION = "1.0"
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
    mb = bytes_per_sec * 8 / 1_000_000
    return f"{mb:6.2f} Mb/s"


def main():
    app_token = get_app_token()
    session_token = open_session(app_token)

    while True:
        r = requests.get(f"{BASE_URL}/connection/", headers={"X-Fbx-App-Auth": session_token})
        data = r.json()

        if not data.get("success") and data.get("error_code") == "auth_required":
            session_token = open_session(app_token)
            continue

        result = data["result"]
        down = fmt_bytes_per_sec(result["rate_down"])
        up = fmt_bytes_per_sec(result["rate_up"])

        sys_data = requests.get(f"{BASE_URL}/system/", headers={"X-Fbx-App-Auth": session_token}).json()
        temps = " ".join(
            f"{s['name']}: {s['value']}°C" for s in sys_data["result"].get("sensors", [])
        )

        sys.stdout.write(f"\r↓ {down}  ↑ {up}  |  {temps}\x1b[K")
        sys.stdout.flush()
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
