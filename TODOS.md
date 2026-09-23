# TODOS

## Engine

### Network-aware demand ripple

**What:** Replace straight-line distance in `uplift()` with Tube-network distance (stops away, with interchange weighting).

**Why:** A disruption at Stockwell affects Brixton (Victoria line interchange) more than a station the same distance away across the river. Straight-line decay ignores how people actually re-route.

**Context:** Deferred at the Sep 23, 2026 eng review; it was a stretch goal and first on the cut list in `docs/designs/surgesignal.md`. The parser already loads `/Line/{id}/Route/Sequence` for A–B range expansion, so the station graph is mostly there. Build a graph from those sequences, swap `geo.dist()` for a hop-weighted distance behind the same interface, and compare both versions in the backtest before switching. Four weeks of logs can't validate it before Oct 20, so present it as "next steps" in the EurekaDev write-up.

**Effort:** M
**Priority:** P3
**Depends on:** Parser route-sequence expansion (outside voice #1)

## Product

### Fixed-Fare Capture (Approach C)

**What:** When the engine predicts a spike, generate a "no surge, fixed £X" message for the operator's own channel (SMS customer list, social page, office window).

**Why:** It targets the pain the operator actually named: losing customers to Uber's surge who never call the firm, so the firm can't see them.

**Context:** Rejected as the main build in office hours on Sep 23, 2026 (it needs a customer channel and can't be validated in 4 weeks). It is the named **Phase B fallback** if Assignment Q3 ("do you turn jobs away, or have idle cars and no calls?") shows idle cars and no calls. Uber fares must be labelled estimates: normal fares checked by hand for 3 routes × a 1.5–2.5 surge band. See `docs/designs/surgesignal.md` → "Gate before Phase B".

**Effort:** M
**Priority:** P2
**Depends on:** Oct 1–4 gate result; operator has a reachable customer channel

## Completed
