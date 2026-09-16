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
