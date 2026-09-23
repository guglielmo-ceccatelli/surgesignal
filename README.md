# SurgeSignal

When a London Tube line fails, Uber surges. SurgeSignal tells a fixed-fare minicab dispatcher where the disruption spike will form, so their cars are already nearby and can quote a short pickup time while Uber is 1.5–2.5× the price.

It predicts **disruption-linked demand risk**, not actual trips: there is no public ride-level data for London. Every number comes with a range, a plain-English reason, and parameters labelled as evidence or assumption (`surgesignal/params.yaml`).

Status: in development for EurekaDev 2026 (Coding Track, Economics). Design: [`docs/designs/surgesignal.md`](docs/designs/surgesignal.md).

## How it works

```
TfL status + disruptions (60 s) ─┐
BikePoint docks (5 min, proxy) ──┼─► raw logger ─► parser ─► engine ─► Telegram alert to dispatcher
Open-Meteo weather, events ──────┘                             │
                                                               └─► Streamlit console (what-if, backtest)
```

Engine, per station: `uplift = Σ severity × exp(−distance / λ) × time profile`, then `pct = (1 + uplift) × weather × events − 1`, ranked by `baseline × pct`.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                 # tests
.venv/bin/python -m workers.logger            # raw TfL logger (data/raw, gzipped, change-only)
.venv/bin/python -m workers.alerter           # Telegram alerts (needs config.local.yaml)
```

Dispatcher console (map, what-if simulator, how it works):

```bash
.venv/bin/streamlit run console/app.py
```

What would it have sent? Replay recorded TfL status through the real alerter:

```bash
.venv/bin/python -m scripts.replay --area "Clapham Common" --radius 5 --idle 6
.venv/bin/python -m scripts.replay --src backup/raw/status --date 2026-09-23
```

Run as background services (macOS; restart on crash, start at login, watchdog every 5 min):

```bash
.venv/bin/python -m scripts.launchd install     # or: status | uninstall
```

Logs go to `data/logs/`. Nothing runs while the Mac sleeps: `caffeinate -s` keeps it awake on the charger.

Alerts setup: copy `config.example.yaml` to `config.local.yaml`, add a bot token from @BotFather, start the alerter, send `/whoami` to your bot and paste the chat id back in as `dispatcher_chat_id`, restart, then `/area Clapham Common 5` and `/idle 6`. Supabase is optional: without it, state lives in `data/state/`; with it, run `supabase/schema.sql` once.

Big events: add fixtures to `events.csv` (venue from `surgesignal/data/venues.csv`, name, end time in London time); the alerter re-reads it every minute. Station busyness comes from `surgesignal/data/baseline.json`; rebuild it with `python -m scripts.build_baseline --fetch` (needs `requirements-dev.txt`).

Optional: `TFL_APP_KEY` raises TfL's anonymous rate limit.

## Data

Contains public sector information licensed under the Open Government Licence v2.0 (Transport for London Unified API).
