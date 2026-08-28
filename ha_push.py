"""Push dei dati Volvo verso Home Assistant via REST API — pensato per cron.

Legge tutti gli endpoint read-only della Connected Vehicle API e crea/aggiorna
sensori in HA (POST /api/states/sensor.<id>), senza integrazioni né MQTT.

Config richiesta nel .env (oltre a quella esistente):
    HA_URL=http://homeassistant.local:8123
    HA_TOKEN=<long-lived access token>   # HA -> Profilo -> Sicurezza -> token

Uso:
    python ha_push.py            # push reale
    python ha_push.py --dry-run  # stampa cosa verrebbe inviato, senza HA

Cron (esempio: ogni 15 min):
    */15 * * * * cd /path/Volvo && .venv/bin/python ha_push.py >> volvo.log 2>&1

Nota rate limit: ~12 chiamate Volvo per esecuzione; ogni 15 min = ~1150/giorno,
ben sotto il limite di 10.000/giorno della API key.

Il lockfile evita esecuzioni sovrapposte (il refresh token Volvo e' a
rotazione: due run concorrenti lo invaliderebbero a vicenda).
"""

import argparse
import fcntl
import json
import os
import sys
import time

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

LOCK_FILE = os.path.join(BASE_DIR, ".ha_push.lock")

# Endpoints da leggere e relativo nome sensore in HA.
# state_fn riceve il dict "data" dell'endpoint e ritorna lo stato del sensore.
NO_WARN = {"NO_WARNING", "UNSPECIFIED", None}


def _count_warnings(data: dict) -> str:
    return str(sum(1 for f in data.values() if isinstance(f, dict) and f.get("value") not in NO_WARN))


def _count_open(data: dict) -> str:
    return str(sum(1 for f in data.values() if isinstance(f, dict) and f.get("value") not in ("CLOSED", "LOCKED", None)))


def _get(data: dict, field: str):
    node = data.get(field)
    return node.get("value") if isinstance(node, dict) else None


def _unit(data: dict, field: str):
    node = data.get(field)
    return node.get("unit") if isinstance(node, dict) else None


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def build_sensors(client, vin: str, prefix: str) -> list[dict]:
    """Ritorna la lista dei sensori da pubblicare: {id, state, attributes}."""
    sensors = []

    def add(name: str, state, attributes: dict, unit=None, device_class=None, icon=None, state_class=None):
        if state is None:
            return
        attr = {**attributes, "vin": vin}
        if unit:
            attr["unit_of_measurement"] = unit
        if device_class:
            attr["device_class"] = device_class
        if icon:
            attr["icon"] = icon
        if state_class:
            attr["state_class"] = state_class
        sensors.append({"id": f"sensor.{prefix}_{name}", "state": state, "attributes": attr})

    details = client.vehicle_details(vin)
    model = "veicolo"
    if details.ok:
        d = details.data.get("data", {})
        model = (d.get("descriptions") or {}).get("model") or model
        add("modello", f"{model} {d.get('modelYear', '')}".strip(), {"friendly_name": f"Volvo {model}"}, icon="mdi:car")

    def fetch(label, fn):
        r = fn(vin)
        if not r.ok:
            log(f"  !! {label}: HTTP {r.status} — {r.error}")
            return None
        return r.data.get("data", {})

    if (d := fetch("odometer", client.vehicle_odometer)):
        add("odometro", _get(d, "odometer"), {"friendly_name": f"{model} Odometro"},
            unit=_unit(d, "odometer"), device_class="distance", icon="mdi:counter",
            state_class="total_increasing")

    if (d := fetch("fuel", client.vehicle_fuel)):
        add("batteria", _get(d, "batteryChargeLevel"), {"friendly_name": f"{model} Batteria"},
            unit="%", device_class="battery", icon="mdi:car-battery", state_class="measurement")
        add("carburante", _get(d, "fuelAmount"), {"friendly_name": f"{model} Carburante"},
            unit=_unit(d, "fuelAmount"), icon="mdi:gas-station", state_class="measurement")

    if (d := fetch("statistics", client.vehicle_statistics)):
        add("autonomia_batteria", _get(d, "distanceToEmptyBattery"),
            {"friendly_name": f"{model} Autonomia"}, unit=_unit(d, "distanceToEmptyBattery"),
            device_class="distance", icon="mdi:map-marker-distance", state_class="measurement")
        add("autonomia_serbatoio", _get(d, "distanceToEmptyTank"),
            {"friendly_name": f"{model} Autonomia serbatoio"}, unit=_unit(d, "distanceToEmptyTank"),
            device_class="distance", icon="mdi:gas-station", state_class="measurement")
        add("consumo_medio_energia", _get(d, "averageEnergyConsumptionAutomatic"),
            {"friendly_name": f"{model} Consumo medio"}, unit=_unit(d, "averageEnergyConsumptionAutomatic"),
            icon="mdi:lightning-bolt", state_class="measurement")
        add("velocita_media", _get(d, "averageSpeedAutomatic"),
            {"friendly_name": f"{model} Velocita media"}, unit=_unit(d, "averageSpeedAutomatic"),
            icon="mdi:speedometer", state_class="measurement")

    if (d := fetch("diagnostics", client.vehicle_diagnostics)):
        add("km_al_tagliando", _get(d, "distanceToService"),
            {"friendly_name": f"{model} Km al tagliando"}, unit=_unit(d, "distanceToService"),
            device_class="distance", icon="mdi:wrench", state_class="measurement")
        add("mesi_al_tagliando", _get(d, "timeToService"),
            {"friendly_name": f"{model} Mesi al tagliando"}, unit=_unit(d, "timeToService"),
            icon="mdi:calendar-wrench")
        add("avviso_tagliando", _get(d, "serviceWarning"),
            {"friendly_name": f"{model} Avviso tagliando"}, icon="mdi:wrench-alert")

    if (d := fetch("engine_status", client.vehicle_engine_status)):
        add("stato_motore", _get(d, "engineStatus"), {"friendly_name": f"{model} Stato motore"},
            icon="mdi:engine")

    # Mappature campo API -> (slug sensore, nome italiano)
    PORTE = {
        "frontLeftDoor": ("porta_ant_sx", "Porta anteriore sinistra"),
        "frontRightDoor": ("porta_ant_dx", "Porta anteriore destra"),
        "rearLeftDoor": ("porta_post_sx", "Porta posteriore sinistra"),
        "rearRightDoor": ("porta_post_dx", "Porta posteriore destra"),
        "tailgate": ("portellone", "Portellone"),
        "hood": ("cofano", "Cofano"),
        "tankLid": ("sportellino_ricarica", "Sportellino ricarica"),
    }
    FINESTRE = {
        "frontLeftWindow": ("finestrino_ant_sx", "Finestrino anteriore sinistro"),
        "frontRightWindow": ("finestrino_ant_dx", "Finestrino anteriore destro"),
        "rearLeftWindow": ("finestrino_post_sx", "Finestrino posteriore sinistro"),
        "rearRightWindow": ("finestrino_post_dx", "Finestrino posteriore destro"),
        "sunroof": ("tetto_apribile", "Tetto apribile"),
    }

    def add_binary(slug: str, aperto: bool, label_it: str, device_class: str):
        sensors.append({
            "id": f"binary_sensor.{prefix}_{slug}",
            "state": "on" if aperto else "off",
            "attributes": {
                "friendly_name": f"{model} {label_it}",
                "vin": vin,
                "device_class": device_class,
            },
        })

    if (d := fetch("doors", client.vehicle_doors)):
        dettagli = {k: v.get("value") for k, v in d.items() if isinstance(v, dict)}
        add("porte_aperte", _count_open(d),
            {"friendly_name": f"{model} Porte aperte", **dettagli}, icon="mdi:car-door")
        for field, (slug, label_it) in PORTE.items():
            if dettagli.get(field) is not None:
                add_binary(slug, dettagli[field] != "CLOSED", label_it, "door")
        if dettagli.get("centralLock") is not None:
            add_binary("sbloccata", dettagli["centralLock"] != "LOCKED", "Sbloccata", "lock")

    if (d := fetch("windows", client.vehicle_windows)):
        dettagli = {k: v.get("value") for k, v in d.items() if isinstance(v, dict)}
        add("finestrini_aperti", _count_open(d),
            {"friendly_name": f"{model} Finestrini aperti", **dettagli}, icon="mdi:car-door")
        for field, (slug, label_it) in FINESTRE.items():
            if dettagli.get(field) is not None:
                add_binary(slug, dettagli[field] != "CLOSED", label_it, "window")

    if (d := fetch("warnings", client.vehicle_warnings)):
        dettagli = {k: v.get("value") for k, v in d.items() if isinstance(v, dict)}
        add("avvisi_attivi", _count_warnings(d),
            {"friendly_name": f"{model} Avvisi attivi", **dettagli}, icon="mdi:alert")

    if (d := fetch("tyres", client.vehicle_tyres)):
        dettagli = {k: v.get("value") for k, v in d.items() if isinstance(v, dict)}
        add("pneumatici_ko", _count_warnings(d),
            {"friendly_name": f"{model} Pneumatici con avvisi", **dettagli}, icon="mdi:tire")

    if (d := fetch("brakes", client.vehicle_brakes)):
        add("avviso_liquido_freni", _get(d, "brakeFluidLevelWarning"),
            {"friendly_name": f"{model} Liquido freni"}, icon="mdi:car-brake-fluid-level")

    if (d := fetch("engine", client.vehicle_engine)):
        # Su EV olio/liquido raffreddamento non si applicano: Volvo risponde
        # UNSPECIFIED. Creiamo i sensori solo se compare un valore reale.
        olio = _get(d, "oilLevelWarning")
        if olio not in (None, "UNSPECIFIED"):
            add("avviso_olio", olio, {"friendly_name": f"{model} Avviso olio"}, icon="mdi:oil-level")
        raffreddamento = _get(d, "engineCoolantLevelWarning")
        if raffreddamento not in (None, "UNSPECIFIED"):
            add("avviso_liquido_raffreddamento", raffreddamento,
                {"friendly_name": f"{model} Liquido raffreddamento"}, icon="mdi:car-coolant-level")

    if (d := fetch("command-accessibility", client.vehicle_command_accessibility)):
        status = d.get("availabilityStatus") or {}
        add("raggiungibilita", status.get("value"),
            {"friendly_name": f"{model} Raggiungibile", "motivo": status.get("unavailableReason")},
            icon="mdi:car-connected")

    if (d := fetch("commands", client.vehicle_commands)):
        # d qui e' una lista di {"command": ..., "href": ...}
        comandi = [c.get("command") for c in d if isinstance(c, dict)]
        add("comandi_disponibili", len(comandi),
            {"friendly_name": f"{model} Comandi disponibili", "comandi": comandi},
            icon="mdi:remote")

    return sensors


def push_to_ha(ha_url: str, ha_token: str, sensors: list[dict], dry_run: bool) -> int:
    """Ritorna il numero di sensori pubblicati con successo."""
    ok = 0
    for s in sensors:
        if dry_run:
            print(json.dumps(s, indent=1, ensure_ascii=False))
            ok += 1
            continue
        try:
            resp = requests.post(
                f"{ha_url}/api/states/{s['id']}",
                headers={
                    "Authorization": f"Bearer {ha_token}",
                    "Content-Type": "application/json",
                },
                json={"state": str(s["state"]), "attributes": s["attributes"]},
                timeout=15,
            )
            if resp.ok:
                ok += 1
            else:
                log(f"  !! HA {s['id']}: HTTP {resp.status_code} — {resp.text[:200]}")
        except requests.RequestException as e:
            log(f"  !! HA {s['id']}: {e}")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="stampa i sensori senza inviare a HA")
    args = parser.parse_args()

    ha_url = os.getenv("HA_URL", "").rstrip("/")
    ha_token = os.getenv("HA_TOKEN")
    if not args.dry_run and not (ha_url and ha_token):
        raise SystemExit("HA_URL / HA_TOKEN mancanti nel .env (oppure usa --dry-run)")

    # Lock: impedisce run sovrapposte (refresh token a rotazione)
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("altra esecuzione in corso, esco")
        sys.exit(0)

    from token_manager import get_access_token
    from volvo_client import VolvoClient

    client = VolvoClient(api_key=os.getenv("apikeyprimary"), access_token=get_access_token())

    vins = [os.getenv("VIN")] if os.getenv("VIN") else [
        v["vin"] for v in (client.list_vehicles().data or {}).get("data", [])
    ]
    if not vins:
        raise SystemExit("nessun veicolo trovato")

    total = 0
    for vin in vins:
        prefix = f"volvo_{vin[-6:].lower()}"
        log(f"veicolo {vin}: raccolgo i dati...")
        sensors = build_sensors(client, vin, prefix)
        pushed = push_to_ha(ha_url, ha_token, sensors, args.dry_run)
        log(f"veicolo {vin}: {pushed}/{len(sensors)} sensori pubblicati")
        total += pushed

    log(f"finito: {total} sensori" + (" (dry-run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
