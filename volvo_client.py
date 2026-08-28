"""Client read-only per la Connected Vehicle API v2 di Volvo Cars.

Documentazione: https://developer.volvocars.com/apis/connected-vehicle/v2/overview/

Autenticazione a due livelli:
  - ``vcc-api-key``  : subscription key del Developer Portal (primaria o secondaria)
  - ``Authorization``: bearer token OAuth2 ottenuto con il code flow Volvo ID
                       (oppure un test token dalla pagina "test access tokens")

Solo endpoint GET (read-only). Gli endpoint di comando (lock/unlock, clima, ecc.)
sono POST e sono volutamente esclusi.
"""

from dataclasses import dataclass

import requests

BASE_URL = "https://api.volvocars.com/connected-vehicle/v2"
TIMEOUT = 15


@dataclass
class ApiResponse:
    ok: bool
    status: int
    data: dict | list | None
    error: str | None = None


class VolvoClient:
    def __init__(self, api_key: str, access_token: str | None = None):
        self.api_key = api_key
        self.access_token = access_token

    def _headers(self) -> dict:
        headers = {
            "accept": "application/json",
            "vcc-api-key": self.api_key,
        }
        if self.access_token:
            headers["authorization"] = f"Bearer {self.access_token}"
        return headers

    def _get(self, path: str) -> ApiResponse:
        try:
            resp = requests.get(
                f"{BASE_URL}{path}", headers=self._headers(), timeout=TIMEOUT
            )
        except requests.RequestException as e:
            return ApiResponse(ok=False, status=0, data=None, error=str(e))

        try:
            body = resp.json()
        except ValueError:
            body = None

        if resp.ok:
            return ApiResponse(ok=True, status=resp.status_code, data=body)

        message = None
        if isinstance(body, dict):
            # Formato errori Volvo: {"error": {"message": ...}} oppure
            # formato APIM: {"message": ...}
            message = (
                (body.get("error") or {}).get("message")
                or body.get("message")
                or str(body)
            )
        return ApiResponse(ok=False, status=resp.status_code, data=body, error=message)

    # ---- endpoint read-only -------------------------------------------------

    def list_vehicles(self) -> ApiResponse:
        """Elenco dei veicoli collegati all'account Volvo ID."""
        return self._get("/vehicles")

    def vehicle_details(self, vin: str) -> ApiResponse:
        return self._get(f"/vehicles/{vin}")

    def vehicle_diagnostics(self, vin: str) -> ApiResponse:
        return self._get(f"/vehicles/{vin}/diagnostics")

    def vehicle_odometer(self, vin: str) -> ApiResponse:
        return self._get(f"/vehicles/{vin}/odometer")

    def vehicle_fuel(self, vin: str) -> ApiResponse:
        return self._get(f"/vehicles/{vin}/fuel")

    def vehicle_brakes(self, vin: str) -> ApiResponse:
        return self._get(f"/vehicles/{vin}/brakes")

    def vehicle_doors(self, vin: str) -> ApiResponse:
        return self._get(f"/vehicles/{vin}/doors")

    def vehicle_windows(self, vin: str) -> ApiResponse:
        return self._get(f"/vehicles/{vin}/windows")

    def vehicle_tyres(self, vin: str) -> ApiResponse:
        return self._get(f"/vehicles/{vin}/tyres")

    def vehicle_warnings(self, vin: str) -> ApiResponse:
        return self._get(f"/vehicles/{vin}/warnings")

    def vehicle_engine_status(self, vin: str) -> ApiResponse:
        return self._get(f"/vehicles/{vin}/engine-status")

    def vehicle_engine(self, vin: str) -> ApiResponse:
        return self._get(f"/vehicles/{vin}/engine")

    def vehicle_statistics(self, vin: str) -> ApiResponse:
        return self._get(f"/vehicles/{vin}/statistics")

    def vehicle_commands(self, vin: str) -> ApiResponse:
        """Elenca i comandi disponibili (lettura; non li esegue)."""
        return self._get(f"/vehicles/{vin}/commands")

    def vehicle_command_accessibility(self, vin: str) -> ApiResponse:
        return self._get(f"/vehicles/{vin}/command-accessibility")

    # ---- comandi (POST, richiedono scope write) ------------------------------

    def send_command(self, vin: str, command: str) -> ApiResponse:
        """Invia un comando al veicolo (lock, unlock, climatization-start, ...).

        I comandi sono asincroni: HTTP 200/202 significa 'accettato da Volvo',
        l'auto lo esegue quando e' raggiungibile (vedi command-accessibility).
        """
        path = f"/vehicles/{vin}/commands/{command}"
        # Volvo richiede Content-Type JSON anche con body vuoto, altrimenti 415
        headers = {**self._headers(), "content-type": "application/json"}
        try:
            resp = requests.post(
                f"{BASE_URL}{path}", headers=headers, data="{}", timeout=TIMEOUT
            )
        except requests.RequestException as e:
            return ApiResponse(ok=False, status=0, data=None, error=str(e))

        try:
            body = resp.json()
        except ValueError:
            body = None

        if resp.ok:
            return ApiResponse(ok=True, status=resp.status_code, data=body)

        message = None
        if isinstance(body, dict):
            message = (
                (body.get("error") or {}).get("message")
                or body.get("message")
                or str(body)
            )
        return ApiResponse(ok=False, status=resp.status_code, data=body, error=message)
