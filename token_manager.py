"""Gestione automatica dei token Volvo ID.

Dopo il primo login (oauth_login.py, una tantum con browser), il REFRESH_TOKEN
nel .env permette di ottenere nuovi access token senza interazione:

    from token_manager import get_access_token
    token = get_access_token()   # rinnova automaticamente se serve

Il refresh token Volvo e' a rotazione: ogni rinnovo ne restituisce uno nuovo,
che viene salvato subito nel .env.
"""

import os
import time

import requests
from dotenv import load_dotenv

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
ISSUER = "https://volvoid.eu.volvocars.com"

# margine di sicurezza sulla scadenza (secondi)
EXPIRY_MARGIN = 120


def _update_env(updates: dict) -> None:
    """Aggiorna chiavi nel .env preservando il resto."""
    with open(ENV_FILE) as f:
        lines = f.read().splitlines()
    written = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else None
        if key in updates:
            out.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in written:
            out.append(f"{key}={value}")
    with open(ENV_FILE, "w") as f:
        f.write("\n".join(out) + "\n")


def _token_endpoint() -> str:
    resp = requests.get(f"{ISSUER}/.well-known/openid-configuration", timeout=15)
    resp.raise_for_status()
    return resp.json()["token_endpoint"]


def refresh_access_token() -> str:
    """Usa il REFRESH_TOKEN del .env per ottenere un nuovo access token."""
    load_dotenv(ENV_FILE, override=True)
    refresh_token = os.getenv("REFRESH_TOKEN")
    client_id = os.getenv("CLIENT_ID")
    client_secret = os.getenv("CLIENT_SECRET")

    if not all([refresh_token, client_id, client_secret]):
        raise RuntimeError(
            "Mancano REFRESH_TOKEN / CLIENT_ID / CLIENT_SECRET nel .env.\n"
            "Fai il primo login con: python oauth_login.py"
        )

    resp = requests.post(
        _token_endpoint(),
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    if not resp.ok:
        raise RuntimeError(
            f"Refresh fallito (HTTP {resp.status_code}): {resp.text[:300]}\n"
            "Il refresh token potrebbe essere scaduto/revocato: "
            "rilancia oauth_login.py"
        )

    tokens = resp.json()
    updates = {
        "ACCESS_TOKEN": tokens["access_token"],
        "TOKEN_EXPIRY": str(int(time.time()) + tokens.get("expires_in", 3600)),
    }
    if tokens.get("refresh_token"):
        updates["REFRESH_TOKEN"] = tokens["refresh_token"]
    _update_env(updates)
    return tokens["access_token"]


def get_access_token() -> str:
    """Ritorna un access token valido, rinnovandolo solo se scaduto."""
    load_dotenv(ENV_FILE, override=True)
    token = os.getenv("ACCESS_TOKEN")
    expiry = int(os.getenv("TOKEN_EXPIRY") or 0)

    if token and time.time() < expiry - EXPIRY_MARGIN:
        return token
    return refresh_access_token()
