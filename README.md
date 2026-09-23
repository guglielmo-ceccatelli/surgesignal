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

Alerts setup: copy `config.example.yaml` to `config.local.yaml`, add a bot token from @BotFather, start the alerter, send `/whoami` to your bot and paste the chat id back in as `dispatcher_chat_id`, restart, then `/area Clapham Common 5` and `/idle 6`. Supabase is optional: without it, state lives in `data/state/`; with it, run `supabase/schema.sql` once.

Optional: `TFL_APP_KEY` raises TfL's anonymous rate limit.

## Data

Contains public sector information licensed under the Open Government Licence v2.0 (Transport for London Unified API).
