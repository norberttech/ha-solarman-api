# Home Assistant Solarman Integration

Integration for Solarman solar inverters and battery systems, providing real-time monitoring and control capabilities
within Home Assistant.

# Installation

Before installing, make sure you have a Solarman API Access.
To get it, you will need to email customerservice@solarmanpv.com and ask for API access.

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=norberttech&repository=ha-solarman-api&category=integration)

Requires [HACS](https://hacs.xyz/) to be installed. After adding, restart Home Assistant and add the integration from
**Settings -> Devices & services -> Add Integration -> "Solarman API"**.

# Why this integration?

Recently the company that installed my solar panels upgraded firmware on the inverter, battery,
and loggers.

Some of those upgrades broke the [existing integration](https://github.com/davidrapan/ha-solarman) that I was using.
After spending some time debugging the old integration and trying to fix it, I decided to move to
the official Solarman API instead of reverse-engineering those devices' local network protocols.

# Tested With

- Inverter: HYD 10KTL-3PH
    - Logger: Data Logger Wifi Solarman LSW-3 USB
- Battery: Pylontech Force_H2 14

> The integration should work with any Solarman device supported by the API, but I only have the above hardware to
> test with. If you have other Solarman models and want to contribute testing or code, please open an issue or PR!

# How it Works

Cloud-polling against `https://globalapi.solarmanpv.com`:

| When               | Endpoint                        | Purpose                                                                                |
|--------------------|---------------------------------|----------------------------------------------------------------------------------------|
| Setup + on 401     | `POST /account/v1.0/token`      | Get bearer token                                                                       |
| Setup              | `POST /station/v1.0/list`       | Discover station                                                                       |
| Setup              | `POST /station/v1.0/device`     | Enumerate devices                                                                      |
| Every cycle        | `POST /device/v1.0/currentData` | Fetch sensor values (per device)                                                       |
| Service calls only | `POST /device/v1.0/historical`  | `import_historical_statistics` (Energy Dashboard backfill) + ad-hoc `fetch_historical` |

Default poll interval: 5 minutes, configurable 5–60 via the integration options. The Solarman cloud refreshes on
the same 5-min cadence, so faster polling is wasted.

Two services expose `/device/v1.0/historical`:

- **`solarman_api.import_historical_statistics`** - backfills HA long-term statistics (solar production, grid
  import/export, home consumption, battery charge/discharge) so the Energy Dashboard shows real history. Defaults to
  the last 180 days. Call it once after installing and again whenever you want to fill gaps. Idempotent -
  already-imported days are skipped. Solarman caps each query at 31 days so wider ranges are chunked automatically.
- **`solarman_api.fetch_historical`** - returns the raw `paramDataList` for an arbitrary date range / time
  granularity (5-min, daily, monthly) without touching statistics. Useful for ad-hoc queries and automations.

Both are invoked from **Developer tools -> Actions** in the HA UI.

# Energy Dashboard setup

After adding the integration, go to **Settings -> Dashboards -> Energy** and wire up the six `total_increasing`
kWh sensors below. Assumes the inverter device was left at the default name — swap the prefix if you renamed it.

| Energy Dashboard field            | Entity                                      |
|-----------------------------------|---------------------------------------------|
| Grid connection -> Imported       | `sensor.<inverter>_grid_import_total`       |
| Grid connection -> Exported       | `sensor.<inverter>_grid_export_total`       |
| Solar panels -> Solar production  | `sensor.<inverter>_solar_production_total`  |
| Home battery -> Energy charged    | `sensor.<inverter>_battery_charge_total`    |
| Home battery -> Energy discharged | `sensor.<inverter>_battery_discharge_total` |

`Home consumption total` exists as `sensor.<inverter>_home_consumption_total` but the Energy Dashboard computes
consumption from the grid/solar/battery flows — don't wire it in yourself unless you want a redundant source.

## Optional: inverter losses as an individual device

The integration exposes a derived pair of sensors that account for the power the inverter itself burns
(electronics, cooling, AC/DC conversion, battery round-trip):

| Entity                                        | Unit | Purpose                                                  |
|-----------------------------------------------|------|----------------------------------------------------------|
| `sensor.<inverter>_inverter_energy`           | W    | Live rate — instantaneous loss at the current moment     |
| `sensor.<inverter>_inverter_energy_total`     | kWh  | Cumulative, Energy Dashboard-compatible (total_increasing) |

Add the `_total` one under **Individual electrical devices -> Add device** to see the inverter as a tracked
consumer in your daily breakdown. Both values are computed from the energy-balance equation (inflows −
outflows across the inverter boundary), not reported natively by Solarman — they include battery round-trip
losses, so expect the cumulative number to be higher than "pure inverter standby" would suggest.

## Optional: live power-flow animation

Each of the three sections above has an optional **power sensor** field. Adding these enables the pulsing-kW flow
diagram at the top of the Energy Dashboard. They don't affect the daily bars / history.

| Section      | Field                     | Option   | Entity                                     |
|--------------|---------------------------|----------|--------------------------------------------|
| Grid         | Type of power measurement | Inverted | `sensor.<inverter>_grid_power` (`PG_Pt1`)  |
| Solar panels | Solar production power    | Standard | `sensor.<inverter>_pv_power` (`PVTP`)      |
| Home battery | Type of power measurement | Standard | `sensor.<inverter>_battery_power` (`B_P1`) |

The Solarman API reports grid power positive while **exporting**, which is the opposite of HA's default — hence
`Inverted` for the grid field. For solar and battery, positive = producing/discharging matches HA's default, so use
`Standard`. If any flow arrow points the wrong way after saving, flip between `Standard` / `Inverted`.

## Backfill history

Call `solarman_api.import_historical_statistics` once from **Developer tools -> Actions** to backfill up to 180 days
of history into the total-increasing sensors. The Energy Dashboard defaults to "Today"; switch to "Last 30 days" or
"Last 12 months" to see the imported range.

# Development

Step-by-step for running the integration against a real Solarman account in a local Home Assistant container.

## 1. Enter the dev shell

```
nix-shell
```

This provides Python 3.13, `uv`, `ruff`, `mypy`, and `just`. The first entry creates `./.venv/` via `uv sync`.

## 2. Configure credentials

```
cp .env.dist .env
```

Edit `.env` and fill in the four Solarman API fields (email, password, app ID, app secret). The file is gitignored.

## 3. Verify the API client against the real API

```
just test-live
```

Hits `globalapi.solarmanpv.com` using the creds from `.env`. Expected output: authenticated, 1 station, list of devices,
per-device key count. Skips automatically if any `SOLARMAN_*` variable is missing from the environment.

To run just the offline unit suite (21 tests, no network):

```
just test
```

## 4. Start Home Assistant in Docker

```
just ha-up
```

Brings up `homeassistant/home-assistant:stable` on `http://localhost:8123`, bind-mounting `./custom_components` into
`/config/custom_components` so the integration is loaded without any copy step. Config state persists in
`./ha-config/` (gitignored).

Useful during iteration:

- `just ha-logs` - tail the container logs.
- `just ha-restart` - reload HA after editing integration code (no image rebuild needed).
- `just ha-down` - stop the container (config is preserved).

## 5. Complete HA onboarding (one time)

Open `http://localhost:8123` and run through the wizard. Pick any credentials - they only protect this local dev
instance. Onboarding state persists in `./ha-config/`, so subsequent `ha-up` runs skip it.

## 6. Add the Solarman integration

In the HA UI: **Settings -> Devices & services -> Add Integration -> "Solarman API"**.

Fill the four fields with the same values from `.env`. On success:

- One config entry is created (`Solarman (<station name>)`).
- Two HA Devices appear: the inverter (37 entities) and the battery (14 entities). `COLLECTOR` sticks are intentionally
  skipped.
- Primary energy/power sensors are tagged for the Energy Dashboard; per-phase, temperature, and status entities land
  under the Diagnostic section.

## 7. Iterate on integration code

After editing anything under `custom_components/solarman_api/`:

```
just ha-restart
```

The container restarts and picks up the change. If you edited `manifest.json` or added files, a restart is required for
HA to re-scan.

## 8. Change the update interval

The integration polls every 5 minutes by default (the API itself caches on that cadence, so faster is wasted). To change
it after setup: **Settings -> Devices & services -> Solarman API -> ⋮ -> Configure -> Update
interval (minutes)**. Allowed range: 5–60.

## Quick reference: just recipes

| Recipe                                              | Purpose                                      |
|-----------------------------------------------------|----------------------------------------------|
| `just test`                                         | Unit suite (21 tests, no network)            |
| `just test-live`                                    | End-to-end API smoke test using `.env` creds |
| `just lint` / `just format`                         | ruff                                         |
| `just typecheck`                                    | mypy                                         |
| `just ha-up` / `ha-down` / `ha-restart` / `ha-logs` | Docker compose wrappers                      |
| `just ha-reset`                                     | Wipe `ha-config/` and start a fresh HA       |
