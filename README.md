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

Engine, per station: `uplift = Σ severity × exp(−distance / λ(t)) × time profile`, where λ grows the longer a disruption lasts (the ripple), then `pct = (1 + uplift) × weather × events − 1`, ranked by the extra riders the disruption adds. Live alerts are for unplanned disruptions; planned works go in the evening look-ahead.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                 # tests
.venv/bin/python -m workers.logger            # raw TfL logger (data/raw, gzipped, change-only)
.venv/bin/python -m workers.alerter           # Telegram alerts (needs config.local.yaml)
```

Website (Overview replay, Try a disruption, The economics, How it works, Evidence, Live):

```bash
.venv/bin/streamlit run console/app.py
```

Links: `?tab=evidence` (or `try`, `economics`, `how`, `live`) opens a tab; `?scenario=northern-rain` (or `central-weekend`, `district-evening`) opens a preset.

What would it have sent? Replay recorded TfL status through the real alerter:

```bash
.venv/bin/python -m scripts.replay --area "Clapham Common" --radius 5 --idle 6
.venv/bin/python -m scripts.replay --src backup/raw/status --date 2026-09-23
```

Evidence (both write a small JSON file under `surgesignal/data/` that the website reads, so commit it after running):

```bash
.venv/bin/python -m scripts.evidence_status   # how many alerts a week a dispatcher would get (T8)
.venv/bin/python -m scripts.backtest          # SurgeSignal vs two common-sense rules, on bike docks (T11)
```

Run as background services (macOS; restart on crash, start at login, watchdog every 5 min):

```bash
.venv/bin/python -m scripts.launchd install     # or: status | uninstall
```

Logs go to `data/logs/`. Nothing runs while the Mac sleeps: `caffeinate -s` keeps it awake on the charger.

Alerts setup: copy `config.example.yaml` to `config.local.yaml`, add a bot token from @BotFather, start the alerter, send `/whoami` to your bot and paste the chat id back in as `dispatcher_chat_id`, restart, then `/area Clapham Common 5` and `/idle 6`. Supabase is optional: without it, state lives in `data/state/`; with it, run `supabase/schema.sql` once.

Big events: add fixtures to `events.csv` (venue from `surgesignal/data/venues.csv`, name, end time in London time); the alerter re-reads it every minute. Station busyness comes from `surgesignal/data/baseline.json`; rebuild it with `python -m scripts.build_baseline --fetch` (needs `requirements-dev.txt`).

Optional: `TFL_APP_KEY` raises TfL's anonymous rate limit.

## Deploy (Streamlit Community Cloud)

1. Sign in at share.streamlit.io with the GitHub account that owns this repo.
2. Create app → repository `surgesignal`, branch `main`, main file `console/app.py`.
3. Advanced settings → Python 3.12. No secrets are needed.

The public copy has no `data/` folder, so Live shows the recorded 23 September evening, and Evidence and The economics read the committed JSON files. Rerun the two evidence scripts and push to update them.

## Data

Contains public sector information licensed under the Open Government Licence v2.0 (Transport for London Unified API).
