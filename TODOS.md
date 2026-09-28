# TODOS

## Engine

### Network-aware demand ripple

**What:** Replace straight-line distance in `uplift()` with Tube-network distance (stops away, with interchange weighting).

**Why:** A disruption at Stockwell affects Brixton (Victoria line interchange) more than a station the same distance away across the river. Straight-line decay ignores how people actually re-route.

**Context:** Deferred at the Sep 23, 2026 eng review; it was a stretch goal and first on the cut list in `docs/designs/surgesignal.md`. The parser already loads `/Line/{id}/Route/Sequence` for A–B range expansion, so the station graph is mostly there. Build a graph from those sequences, swap `geo.dist()` for a hop-weighted distance behind the same interface, and compare both versions in the backtest before switching. Four weeks of logs can't validate it before Oct 20, so present it as "next steps" in the EurekaDev write-up.

**Effort:** M
**Priority:** P3
**Depends on:** Parser route-sequence expansion (outside voice #1)

### Alert fatigue: how many alerts a week is too many?

**What:** Decide, with an operator, the alert rate a dispatcher will actually act on, then tune to it: raise `alert_threshold_uplift`, cap alerts per day, or only alert in the firm's busy hours.

**Why:** The T8 count (Sep 28) found 14–47 actionable alerts a week per 5 km area (median 23), plus line-wide notices. A dispatcher who gets three a day may start ignoring them, and then the one that matters is missed.

**Context:** The count comes from 27 h of logs, so the rate is rough. Same-line repeats are already merged (one message per line per problem). The threshold (0.15) is an ESTIMATE; the backtest (T11) can show whether weaker alerts predict any bike movement at all, which is the data-side argument for raising it.

**Effort:** S
**Priority:** P2
**Depends on:** Operator interviews; T11 results

## Console

### Live data on the public website (Supabase)

**What:** The alerter also writes its state to Supabase; the hosted console's Live tab reads the public read-only views instead of showing a labelled recording.

**Why:** A live demo on the public link is stronger evidence than a recording, and makes the online Live tab useful to a dispatcher.

**Context:** Deferred in the design review on Sep 24, 2026 (decision 2A: online Live shows the latest recorded snapshot with a RECORDED badge). `supabase/schema.sql` (tables + RLS + `public_disruptions` / `public_checkins` views) and `SupabaseStore` already exist; the console needs an anon-key reader for the two views and the heartbeat. Start by creating the Supabase project, running the schema, and putting the keys in `config.local.yaml` and Streamlit secrets.

**Effort:** M
**Priority:** P2
**Depends on:** A Supabase project (your account)

## Data

### Log the whole week, not just when the laptop is awake

**What:** Run the logger somewhere that stays on: a free always-on host (a small VM, a Raspberry Pi, a friend's server), or keep the Mac awake with the lid open on power.

**Why:** Sep 23–28 logged only ~27 h of 5 days. The GitHub Actions backup was meant to cover the rest but runs about every 4 h, not every 5 min (GitHub delays and drops scheduled workflows). The backtest (T11) needs bike data around each disruption, so every hour asleep is lost evidence.

**Context:** `workers/logger.py` needs only Python and outbound HTTPS. The launchd setup is Mac-only; a systemd unit would be ~10 lines.

**Effort:** S
**Priority:** P1
**Depends on:** Nothing

## Product

### Fixed-Fare Capture (Approach C)

**What:** When the engine predicts a spike, generate a "no surge, fixed £X" message for the operator's own channel (SMS customer list, social page, office window).

**Why:** It targets the pain the operator actually named: losing customers to Uber's surge who never call the firm, so the firm can't see them.

**Context:** Rejected as the main build in office hours on Sep 23, 2026 (it needs a customer channel and can't be validated in 4 weeks). It is the named **Phase B fallback** if Assignment Q3 ("do you turn jobs away, or have idle cars and no calls?") shows idle cars and no calls. Uber fares must be labelled estimates: normal fares checked by hand for 3 routes × a 1.5–2.5 surge band. See `docs/designs/surgesignal.md` → "Gate before Phase B".

**Effort:** M
**Priority:** P2
**Depends on:** Oct 1–4 gate result; operator has a reachable customer channel

### Automatic event fixtures

**What:** Fill `events.csv` automatically from fixture sources (football, concerts, rugby) instead of by hand.

**Why:** The look-ahead and live event boost are only as good as the fixtures someone remembers to type in.

**Context:** Deferred Sep 23, 2026 when T12 was extended to events. Free football fixture APIs (e.g. football-data.org) need a key and sign-up; concerts/arenas need Ticketmaster Discovery (key) or paid event-data services. Keep `events.csv` as the override so hand-entered rows still win. Start with the four football grounds in `surgesignal/data/venues.csv`, then Wembley and The O2. Only worth doing if the operator says events matter to them.

**Effort:** M
**Priority:** P3
**Depends on:** Operator interviews (do events matter to them?)

## Completed
