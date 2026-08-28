"""OAuth2 code flow (PKCE) verso Volvo ID — port Python del sample Node.

Serve SOLO per ottenere access/refresh token (autorizzazione), non tocca
i dati del veicolo. Richiede nel .env:

    CLIENT_ID=...
    CLIENT_SECRET=...
    REDIRECT_URI=http://localhost:3000/callback   # deve combaciare col portale
    SCOPES=openid conve:vehicle_relation ...      # scope separati da spazio

Uso:
    python oauth_login.py

Apre il browser sul login Volvo ID; dopo il consenso salva ACCESS_TOKEN e
REFRESH_TOKEN nel .env, pronti per test_keys.py.
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from dotenv import load_dotenv

load_dotenv()

ISSUER = "https://volvoid.eu.volvocars.com"
ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:3000/callback")

parser = argparse.ArgumentParser(description="Login OAuth2 Volvo ID (code flow + PKCE)")
parser.add_argument(
    "--scopes",
    default=os.getenv("SCOPES", "openid"),
    help='Scope separati da spazio (default: SCOPES dal .env). '
         'Esempio: --scopes "openid conve:vehicle_relation"',
)
_args = parser.parse_args()
SCOPES = _args.scopes

_result: dict = {}


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def save_tokens(tokens: dict) -> None:
    """Aggiunge/aggiorna i token nel .env (con scadenza per il rinnovo automatico)."""
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            lines = [
                l for l in f.read().splitlines()
                if not l.startswith(("ACCESS_TOKEN=", "REFRESH_TOKEN=", "TOKEN_EXPIRY="))
            ]
    lines.append(f"ACCESS_TOKEN={tokens['access_token']}")
    lines.append(f"TOKEN_EXPIRY={int(time.time()) + tokens.get('expires_in', 3600)}")
    if tokens.get("refresh_token"):
        lines.append(f"REFRESH_TOKEN={tokens['refresh_token']}")
    with open(ENV_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Token salvati in {ENV_FILE}")


def main() -> None:
    if not CLIENT_ID or not CLIENT_SECRET:
        raise SystemExit(
            "CLIENT_ID / CLIENT_SECRET mancanti nel .env.\n"
            "Si ottengono pubblicando un'applicazione su "
            "https://developer.volvocars.com/account/"
        )

    discovery = requests.get(f"{ISSUER}/.well-known/openid-configuration", timeout=15)
    discovery.raise_for_status()
    endpoints = discovery.json()

    verifier, challenge = pkce_pair()
    parsed = urllib.parse.urlparse(REDIRECT_URI)
    callback_path = parsed.path or "/callback"
    port = parsed.port or 3000

    auth_params = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    login_url = f"{endpoints['authorization_endpoint']}?{auth_params}"

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed_url = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed_url.query)
            if parsed_url.path != callback_path:
                self._reply(404, b"Pagina non trovata.")
                return
            if "code" in params:
                _result["code"] = params["code"][0]
                self._reply(200, b"Login completato, puoi chiudere questa pagina.")
            elif "error" in params:
                _result["error"] = params["error"][0]
                _result["error_description"] = params.get("error_description", [""])[0]
                self._reply(400, b"Login fallito: vedi il terminale per i dettagli.")
            else:
                self._reply(400, b"Callback non valida.")

        def _reply(self, status, body):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silenzia i log del server
            pass

    server = HTTPServer(("localhost", port), CallbackHandler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    print(f"Apro il browser per il login Volvo ID...\n{login_url}\n")
    webbrowser.open(login_url)

    while "code" not in _result and "error" not in _result:
        threading.Event().wait(0.5)
    server.server_close()

    if "error" in _result:
        raise SystemExit(
            f"Volvo ID ha rifiutato il login: {_result['error']}\n"
            f"Dettaglio: {_result.get('error_description', '')}\n\n"
            "Se l'errore e' invalid_scope: riduci SCOPES nel .env a quelli\n"
            "effettivamente selezionati in fase di publish dell'app."
        )

    token_resp = requests.post(
        endpoints["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": _result["code"],
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
        timeout=15,
    )
    token_resp.raise_for_status()
    tokens = token_resp.json()
    save_tokens(tokens)
    print(f"Access token (primi 20 char): {tokens['access_token'][:20]}...")
    print("Ora puoi rilanciare: python test_keys.py")


if __name__ == "__main__":
    main()
