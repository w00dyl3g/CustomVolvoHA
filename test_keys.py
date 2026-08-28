"""Test delle chiavi API Volvo Cars (read-only).

Uso:
    python test_keys.py

Verifica:
  1. apikeyprimary / apikeysecondary (VCC API key del Developer Portal)
     - chiave non valida  -> APIM risponde 401 "subscription key"
     - chiave valida      -> la richiesta passa il gateway e risponde 401
       perche' manca il bearer token (oppure 200 se ACCESS_TOKEN e' presente)
  2. Se ACCESS_TOKEN e' presente nel .env, esegue le chiamate read-only
     complete: lista veicoli + dettagli/odometro/carburante per ogni VIN.

Il code flow OAuth2 (CLIENT_ID/CLIENT_SECRET) non e' richiesto per questo test:
serve solo quando vorrai ottenere token per un account Volvo ID reale.
"""

import os
import sys

from dotenv import load_dotenv

from volvo_client import VolvoClient

load_dotenv()

PRIMARY = os.getenv("apikeyprimary")
SECONDARY = os.getenv("apikeysecondary")
VIN = os.getenv("VIN")


def get_token() -> str | None:
    """Access token valido: rinnovo automatico via refresh token, se presente."""
    try:
        from token_manager import get_access_token
        return get_access_token()
    except Exception as e:
        print(f"  {WARN} nessun token utilizzabile: {e}")
        return None

OK = "\033[92m✔\033[0m"
KO = "\033[91m✘\033[0m"
WARN = "\033[93m⚠\033[0m"


def test_key(name: str, key: str | None, token: str | None) -> bool:
    """Ritorna True se la chiave e' valida."""
    print(f"\n== Test {name} ==")
    if not key:
        print(f"  {WARN} variabile '{name}' non trovata nel .env, salto")
        return False

    client = VolvoClient(api_key=key, access_token=token)
    resp = client.list_vehicles()

    if resp.ok:
        count = len((resp.data or {}).get("data", []))
        print(f"  {OK} chiave VALIDA (HTTP {resp.status}) — {count} veicoli visibili")
        return True

    error = (resp.error or "").lower()
    if "subscription key" in error:
        print(f"  {KO} chiave NON VALIDA (HTTP {resp.status}): {resp.error}")
        return False

    if resp.status == 401:
        print(f"  {OK} chiave VALIDA (HTTP {resp.status}: il gateway l'ha accettata,")
        print(f"     ma serve un access token per leggere i dati: {resp.error})")
        return True

    print(f"  {WARN} risposta inattesa HTTP {resp.status}: {resp.error}")
    return False


def test_read_endpoints(token: str | None) -> None:
    """Chiamate read-only complete, solo se abbiamo un access token."""
    if not token:
        print(f"\n{WARN} Nessun access token: salto le chiamate dati.")
        print("   Primo accesso: compila CLIENT_ID/CLIENT_SECRET e lancia oauth_login.py.")
        print("   Poi il token si rinnova da solo a ogni esecuzione.")
        return

    print("\n== Test endpoint read-only (con access token) ==")
    client = VolvoClient(api_key=PRIMARY, access_token=token)

    if VIN:
        # VIN esplicito nel .env: si testa direttamente quello
        items = [{"vin": VIN}]
        print(f"  Uso il VIN dal .env: {VIN}")
    else:
        vehicles = client.list_vehicles()
        if not vehicles.ok:
            print(f"  {KO} list_vehicles fallita: HTTP {vehicles.status} — {vehicles.error}")
            sys.exit(1)
        items = (vehicles.data or {}).get("data", [])
        print(f"  {OK} {len(items)} veicolo/i collegato/i all'account")

    for v in items:
        vin = v.get("vin")
        print(f"\n  Veicolo {vin}")

        details = client.vehicle_details(vin)
        if details.ok:
            d = details.data.get("data", {})
            model = (d.get("descriptions") or {}).get("model", "?")
            print(f"    {OK} details: {model} {d.get('modelYear', '')}")
        else:
            print(f"    {KO} details: HTTP {details.status} — {details.error}")

        for label, fn in [
            ("odometer", client.vehicle_odometer),
            ("fuel", client.vehicle_fuel),
            ("diagnostics", client.vehicle_diagnostics),
            ("warnings", client.vehicle_warnings),
        ]:
            r = fn(vin)
            mark = OK if r.ok else KO
            msg = "ok" if r.ok else f"HTTP {r.status} — {r.error}"
            print(f"    {mark} {label}: {msg}")


def main() -> None:
    print("Test chiavi Volvo Cars Developer Portal")
    print("-" * 45)

    token = get_token()

    primary_ok = test_key("apikeyprimary", PRIMARY, token)
    secondary_ok = test_key("apikeysecondary", SECONDARY, token)

    test_read_endpoints(token)

    print("\n" + "-" * 45)
    print(f"Riepilogo: primary {'OK' if primary_ok else 'KO'}, "
          f"secondary {'OK' if secondary_ok else 'KO'}")
    sys.exit(0 if (primary_ok or secondary_ok) else 1)


if __name__ == "__main__":
    main()
