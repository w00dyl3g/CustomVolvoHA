"""Servizio attuatori Volvo -> Home Assistant.

Piccolo server HTTP che riceve comandi da HA (rest_command) e li inoltra
alla Connected Vehicle API. Pensato per girare sulla stessa macchina del cron
(condivide .env e token con ha_push.py).

Config nel .env:
    COMMAND_TOKEN=<stringa-segreta-lunga>   # obbligatorio: protegge l'endpoint
    COMMAND_PORT=8099                       # opzionale

Avvio (systemd o cron):
    @reboot cd /path/Volvo && .venv/bin/python command_server.py >> volvo_cmd.log 2>&1

Chiamata da HA (rest_command, vedi README):
    POST http://<macchina>:8099/command
    Headers: X-Auth-Token: <COMMAND_TOKEN>
    Body: {"command": "lock"}   # oppure "vin" esplicito

SICUREZZA: chi ha il COMMAND_TOKEN puo' aprire/chiudere l'auto. Tienilo
segreto, usa una stringa lunga e casuale, e non esporre la porta su internet.
"""

import fcntl
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

COMMAND_TOKEN = os.getenv("COMMAND_TOKEN")
PORT = int(os.getenv("COMMAND_PORT", "8099"))
LOCK_FILE = os.path.join(BASE_DIR, ".ha_push.lock")  # stesso lock di ha_push.py

# Solo i comandi che l'auto dichiara di supportare (EX30: no engine-start)
ALLOWED_COMMANDS = {
    "lock", "unlock", "lock-reduced-guard",
    "honk", "flash", "honk-flash",
    "climatization-start", "climatization-stop",
}


def execute(command: str, vin: str | None) -> tuple[int, dict]:
    # Stesso lockfile di ha_push.py: il refresh token e' a rotazione,
    # mai due operazioni token in parallelo.
    with open(LOCK_FILE, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        from token_manager import get_access_token
        from volvo_client import VolvoClient

        client = VolvoClient(api_key=os.getenv("apikeyprimary"), access_token=get_access_token())

        if not vin:
            vehicles = client.list_vehicles()
            if not vehicles.ok or not vehicles.data.get("data"):
                return 502, {"error": "nessun veicolo trovato"}
            vin = vehicles.data["data"][0]["vin"]

        resp = client.send_command(vin, command)

    if resp.ok:
        return 200, {"status": "accepted", "command": command, "vin": vin}
    return resp.status or 502, {"error": resp.error, "command": command}


class CommandHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/command":
            self._reply(404, {"error": "not found"})
            return
        if not COMMAND_TOKEN or self.headers.get("X-Auth-Token") != COMMAND_TOKEN:
            self._reply(401, {"error": "unauthorized"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._reply(400, {"error": "body JSON non valido"})
            return

        command = body.get("command", "")
        if command not in ALLOWED_COMMANDS:
            self._reply(400, {"error": f"comando non ammesso", "ammessi": sorted(ALLOWED_COMMANDS)})
            return

        try:
            status, payload = execute(command, body.get("vin"))
        except Exception as e:
            self._reply(500, {"error": str(e)})
            return
        self._reply(status, payload)

    def _reply(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[cmd] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    if not COMMAND_TOKEN:
        raise SystemExit("COMMAND_TOKEN mancante nel .env — generane uno lungo e casuale")
    server = HTTPServer(("0.0.0.0", PORT), CommandHandler)
    print(f"command server in ascolto su :{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
