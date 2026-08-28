# Volvo Cars → Home Assistant (unofficial)

Python tools to read data from a Volvo car via the official
[Volvo Cars Developer Portal](https://developer.volvocars.com) **Connected Vehicle API v2**
and push it into [Home Assistant](https://www.home-assistant.io) as sensors — no Volvo
integration, no MQTT broker, no cloud account required. Optional remote commands
(lock/unlock, climatization, honk/flash) exposed to HA as actions.

Tested with a Volvo EX30 (2024). Should work with any Volvo supported by the
Connected Vehicle API v2 (model year 2015+ / 2022+ depending on region and model —
check the [API docs](https://developer.volvocars.com/apis/connected-vehicle/v2/overview/)).

> **Disclaimer**: unofficial project, not affiliated with or endorsed by Volvo Car
> Corporation. Based on Volvo's public API documentation and their
> [official API samples](https://github.com/volvo-cars/developer-portal-api-samples).

## Features

- 🔑 **API key validation** against the live gateway (`test_keys.py`)
- 🔐 **OAuth2 authorization code flow with PKCE** (`oauth_login.py`), then fully
  **headless**: access tokens auto-refresh via refresh token rotation (`token_manager.py`)
- 📡 **19+ sensors pushed to Home Assistant** via its REST API (`ha_push.py`):
  battery level, range, odometer, average consumption/speed, service countdown,
  doors/windows/lock per-item binary sensors, warnings, tyres, brakes, reachability
- 🎮 **Actuators**: tiny local HTTP service (`command_server.py`) so HA can trigger
  `lock`, `unlock`, `lock-reduced-guard`, `honk`, `flash`, `honk-flash`,
  `climatization-start`, `climatization-stop` via `rest_command`
- 🔬 **Scope scanner** (`check_scopes.py`): finds which OAuth scopes your published
  app is allowed to request — no browser needed
- ⏱️ **Cron-friendly**: lockfile against concurrent runs (refresh token rotation is
  destructive if raced), rate-limit-aware defaults

## Architecture

```
                    ┌────────────────────────────┐
  one time only     │  oauth_login.py            │   (any PC with a browser)
  (user consent)    │  Volvo ID login → tokens   │
                    └─────────────┬──────────────┘
                                  │ .env (copy once)
                                  ▼
  cron every 15 min   ┌────────────────────────────┐      REST API
  on a headless box   │  ha_push.py                │ ───────────────▶ Home Assistant
                      │  token_manager auto-refresh│                  sensors.*
                      └─────────────┬──────────────┘                  binary_sensors.*
                                    │ GET (read-only)
                                    ▼
                          api.volvocars.com (Connected Vehicle API v2)
                                    ▲
                                    │ POST commands (optional)
                    ┌───────────────┴─────────────┐
  HA rest_command   │  command_server.py :8099    │
  ─────────────────▶│  (shared-secret protected)  │
                    └─────────────────────────────┘
```

## Prerequisites

- A Volvo linked to your **Volvo ID** account
- Python 3.10+
- Home Assistant reachable over HTTP(S) (for the sensor push)
- One machine **with a browser** for the one-time login (the production box can be
  headless — see step 4)

## Step 1 — Get your API keys (Developer Portal)

1. Create an account on the [Volvo Cars Developer Portal](https://developer.volvocars.com).
   Use the **same email as your Volvo ID** (the one linked to the car).
2. On your [account page](https://developer.volvocars.com/account/#your-api-applications),
   create an **API application**.
3. You immediately get a **primary** and a **secondary** VCC API key. These identify
   your application and its quota (10,000 requests/day, shared between the two keys —
   the secondary one exists for zero-downtime key rotation).

The API keys alone are **not** enough: every endpoint also requires a user-consented
OAuth2 access token. That is by design — keys identify *the app*, the token proves
*the car owner* authorized it. On to step 2.

## Step 2 — Publish the application (get CLIENT_ID / CLIENT_SECRET)

1. Under your API application, click **Publish**
   ([Dynamic App Publish](https://developer.volvocars.com/news/dynamic-app-publish/)
   makes this instant since January 2025 — no manual review for personal use).
2. Fill in the required fields:
   - **Redirect URI**: `http://localhost:3000/callback` (must match `.env` exactly)
   - **Scopes**: select the Connected Vehicle API scopes you need. Validated names:

     | Scope | Unlocks |
     |---|---|
     | `openid` | login (always required) |
     | `conve:vehicle_relation` | list vehicles, vehicle details |
     | `conve:odometer_status` | odometer |
     | `conve:fuel_status` · `conve:battery_charge_level` | fuel / EV battery level |
     | `conve:trip_statistics` | average consumption, speed, range |
     | `conve:diagnostics_workshop` · `conve:diagnostics_engine_status` | service counters, diagnostics |
     | `conve:engine_status` · `conve:environment` | engine status/values |
     | `conve:brake_status` · `conve:tyre_status` | brakes, tyres |
     | `conve:doors_status` · `conve:lock_status` · `conve:windows_status` | doors, lock, windows |
     | `conve:warnings` | warnings (note: `conve:warn_status` is **not** a valid name) |
     | `conve:commands` · `conve:command_accessibility` | list commands, reachability |
     | `conve:lock` · `conve:unlock` · `conve:climatization_start_stop` · `conve:honk_flash` | **write** scopes — only if you want remote commands |

     ⚠️ Gotchas learned the hard way:
     - **`offline_access` is not a valid scope** — Volvo returns a refresh token anyway
     - requesting a scope your app wasn't granted → immediate `invalid_scope` error,
       before the login page even renders (that's what `check_scopes.py` exploits)
     - scopes are fixed at consent time: changing them later requires a fresh login
3. Confirm — you are shown the **Client ID** and **Client Secret**. Save the secret
   immediately; it is not displayed again.

## Step 3 — Install

```bash
git clone <your-repo-url> volvo-ha && cd volvo-ha
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then edit .env with your values
```

Minimum `.env` at this point: `apikeyprimary`, `apikeysecondary`, `CLIENT_ID`,
`CLIENT_SECRET`, `REDIRECT_URI`, `SCOPES`.

## Step 4 — One-time login (needs a browser, once)

```bash
.venv/bin/python oauth_login.py
```

A browser opens on the Volvo ID login page. Log in with the Volvo ID that owns the
car, grant consent, done — `ACCESS_TOKEN` / `REFRESH_TOKEN` are written to `.env`.

**Headless production box?** Run this step on any PC with a browser (the
`localhost:3000` redirect is local to that PC — it just works), then copy the `.env`
to the headless machine:

```bash
scp .env user@headless-box:/path/to/volvo-ha/.env
```

From then on everything is headless: `token_manager.py` refreshes the access token
automatically. **Warning**: Volvo refresh tokens rotate — each refresh invalidates
the previous one. Use the tokens from **one machine only**; after the `scp`, don't
run the tools on the source machine anymore. If the chain breaks (long inactivity,
or two machines racing), just redo this step.

Verify everything:

```bash
.venv/bin/python test_keys.py
```

## Step 5 — Push sensors to Home Assistant (cron)

1. In HA: **Profile → Security → Long-lived access tokens** → create one
2. Add to `.env`:
   ```ini
   HA_URL=http://192.168.x.x:8123
   HA_TOKEN=<long-lived token>
   ```
3. Dry-run to preview the sensors: `.venv/bin/python ha_push.py --dry-run`
4. Cron (every 15 minutes):
   ```cron
   */15 * * * * cd /path/to/volvo-ha && .venv/bin/python ha_push.py >> volvo.log 2>&1
   ```

Entities created (suffix = last 6 of VIN, e.g. `sensor.volvo_123456_*`):
`batteria`, `autonomia_batteria`, `odometro`, `consumo_medio_energia`,
`velocita_media`, `km_al_tagliando`, `mesi_al_tagliando`, `avviso_tagliando`,
`stato_motore`, `raggiungibilita`, `comandi_disponibili`, counters for open
doors/windows/active warnings/tyre alerts, plus per-item
`binary_sensor.volvo_*` for each door, window, sunroof, charge flap and the
central lock. Numeric sensors carry `state_class`, so HA long-term statistics
(history graphs) work out of the box. EV-non-applicable fields (oil, coolant)
are skipped automatically.

### Rate limit budget

Each run makes ~15 API calls (+1 token refresh about once an hour).
Limit: **10,000 calls/day per application** (primary + secondary keys share it).

| Cron interval | Calls/day | % of limit |
|---|---|---|
| **15 min (recommended)** | ~1,500 | 15% |
| 5 min | ~4,400 | 44% |
| 2 min | ~11,000 | ❌ over the limit |

Bonus reason not to poll faster: when the car is parked/off, Volvo serves cached
data anyway — 15 min loses you nothing.

## Step 6 — Remote commands (optional actuators)

Requires the **write scopes** from step 2 in your token (append them to `SCOPES`,
then redo step 4 — scopes are fixed at consent time).

1. Generate a shared secret and start the command service:
   ```bash
   echo "COMMAND_TOKEN=$(openssl rand -hex 32)" >> .env
   nohup .venv/bin/python command_server.py >> volvo_cmd.log 2>&1 &
   ```
   Autostart via cron: `@reboot cd /path/to/volvo-ha && .venv/bin/python command_server.py >> volvo_cmd.log 2>&1`
2. In HA `configuration.yaml`:
   ```yaml
   rest_command:
     volvo_lock:
       url: "http://<volvo-box-ip>:8099/command"
       method: post
       headers:
         X-Auth-Token: "<COMMAND_TOKEN>"
       payload: '{"command": "lock"}'
       content_type: application/json
     volvo_unlock:
       url: "http://<volvo-box-ip>:8099/command"
       method: post
       headers:
         X-Auth-Token: "<COMMAND_TOKEN>"
       payload: '{"command": "unlock"}'
       content_type: application/json
     volvo_clima_start:
       url: "http://<volvo-box-ip>:8099/command"
       method: post
       headers:
         X-Auth-Token: "<COMMAND_TOKEN>"
       payload: '{"command": "climatization-start"}'
       content_type: application/json
     volvo_clima_stop:
       url: "http://<volvo-box-ip>:8099/command"
       method: post
       headers:
         X-Auth-Token: "<COMMAND_TOKEN>"
       payload: '{"command": "climatization-stop"}'
       content_type: application/json
   ```
   If HA runs in Docker **without** `network_mode: host`, use the box's LAN IP, not
   `localhost` (inside the container, localhost is the container itself).
3. Reload REST commands (Developer Tools → YAML) or restart HA, then call
   `rest_command.volvo_lock` from Developer Tools → Actions, dashboards
   (`perform-action` tap actions) or automations.

Commands are **asynchronous**: HTTP 200 means "accepted by Volvo"; the car executes
when reachable (check the reachability sensor). `command_server.py` shares the
lockfile with `ha_push.py` so token refreshes never race.

🔒 **Security**: anyone with `COMMAND_TOKEN` can unlock your car. Use a long random
token, keep the port on your LAN, never expose it to the internet.

## File overview

| File | Purpose |
|---|---|
| `volvo_client.py` | Read-only client for Connected Vehicle API v2 (all GET endpoints) + command POST |
| `token_manager.py` | Automatic access-token refresh (rotation-aware, updates `.env`) |
| `oauth_login.py` | One-time OAuth2 code flow + PKCE (`--scopes` to override) |
| `test_keys.py` | Validates API keys; full read test when a token exists |
| `check_scopes.py` | Scans which scopes your app may request (no browser) |
| `ha_push.py` | Collects all data → pushes HA entities (`--dry-run` supported) |
| `command_server.py` | Local HTTP actuator endpoint for HA `rest_command` |
| `api.json` | Reference copy of the Connected Vehicle v2 OpenAPI spec |

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `401 UNAUTHORIZED` on data calls | API key fine, token missing/expired → run `oauth_login.py` |
| `invalid_scope` before login page | a scope in `SCOPES` isn't granted to the app — run `check_scopes.py` |
| `invalid_grant` on refresh | refresh token rotated elsewhere or expired → redo step 4 |
| `403` on a specific endpoint | that scope isn't in your token → add scope, redo login |
| `415` on commands | outdated client — commands require `Content-Type: application/json` (fixed in this repo) |
| HA hostname doesn't resolve | use HA's LAN IP in `HA_URL` (mDNS names can be flaky) |

## License & credits

Choose a license (e.g. MIT/Apache-2.0) when publishing. API design and docs © Volvo
Car Corporation; sample flow based on Volvo's Apache-2.0
[developer-portal-api-samples](https://github.com/volvo-cars/developer-portal-api-samples).
