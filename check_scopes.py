"""Scansione degli scope validi per l'app, SENZA browser.

L'endpoint di autorizzazione Volvo ID valida gli scope prima di mostrare
il login: se lo scope non e' permesso risponde subito con un redirect
error=invalid_scope, altrimenti mostra la pagina di login.

Quindi basta una GET per scope, nessuna credenziale da digitare.

Uso:
    python check_scopes.py
"""

import os
import urllib.parse

import requests
from dotenv import load_dotenv

load_dotenv()

ISSUER = "https://volvoid.eu.volvocars.com"
CLIENT_ID = os.getenv("CLIENT_ID")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:3000/callback")

# Candidati: nomi confermati da progetti noti + varianti plausibili
# documentate nel portale per la Connected Vehicle API v2.
CANDIDATES = [
    # base
    "openid",
    "email",
    "profile",
    "offline_access",
    # confermati (volvocarsapi / thomasddn)
    "conve:battery_charge_level",
    "conve:brake_status",
    "conve:climatization_start_stop",
    "conve:command_accessibility",
    "conve:commands",
    "conve:diagnostics_engine_status",
    # dalla spec api.json
    "conve:vehicle_relation",
    # plausibili read-only
    "conve:vehicle_status",
    "conve:odometer_status",
    "conve:fuel_status",
    "conve:statistics",
    "conve:diagnostics_workshop",
    "conve:engine_status",
    "conve:environment",
    "conve:doors_status",
    "conve:lock_status",
    "conve:windows_status",
    "conve:tyre_status",
    "conve:warn_status",
    "conve:warnings",
    "conve:trip_statistics",
    # write (ci aspettiamo esistano, ma NON li useremo)
    "conve:lock",
    "conve:unlock",
    "conve:engine_start_stop",
    "conve:honk_flash",
]


def check_scope(scope: str) -> str:
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": scope,
    })
    url = f"{ISSUER}/as/authorization.oauth2?{params}"
    try:
        resp = requests.get(url, allow_redirects=False, timeout=15)
    except requests.RequestException as e:
        return f"ERRORE RETE: {e}"

    location = resp.headers.get("Location", "")
    if "invalid_scope" in urllib.parse.unquote(location):
        return "INVALIDO"
    if resp.status_code in (200, 302, 303):
        # pagina di login o redirect verso il login -> scope accettato
        return "VALIDO"
    return f"INCERTO (HTTP {resp.status_code})"


def main() -> None:
    if not CLIENT_ID:
        raise SystemExit("CLIENT_ID mancante nel .env")

    validi = []
    print(f"{'scope':40} esito")
    print("-" * 55)
    for scope in CANDIDATES:
        esito = check_scope(scope)
        if esito == "VALIDO":
            validi.append(scope)
        print(f"{scope:40} {esito}")

    print("\nScope validi (riga SCOPES pronta per il .env):")
    print(" ".join(validi))


if __name__ == "__main__":
    main()
