# SurgeSignal — Transit-Disruption Fleet Coverage Optimizer — PRD

Sep 23, 2026 · @Someone

## 1. Problem Statement & Opportunity

Private-hire and minicab fleets have no way to anticipate demand spikes caused by transit disruptions. Uber's own driver heatmap is reactive, built from ride requests that have already happened, so a fleet operator without that infrastructure is always a step behind. When a Tube line delays or closes, nearby rideshare demand reliably spikes well before enough requests accumulate for a reactive system to notice.

This PRD scopes a predictive layer that surfaces that spike the moment a disruption is reported, combined with driver check-ins that confirm or correct the prediction on the ground, giving fleet dispatchers a real lead-time advantage over reactive systems. The paying customer is a fleet operator, not an individual Uber driver: Uber already gives its own drivers a free heatmap, so competing head-on there is a weak position, while a private-hire or minicab company has no equivalent tool today.

Economic framing: whoever has faster, better information about where demand is forming captures the value first, the same information-advantage logic behind Grossman and Stiglitz's point that costly information has real economic value. The demand ripple itself, spreading outward from a disruption point over time and distance, is also a legitimate small spatial-propagation problem, giving the project a genuine Math angle alongside the Economics one.

## 2. Goals & Success Metrics

| Goal | Metric | Target for demo |
| --- | --- | --- |
| Model flags disruption-driven demand spikes early | Lead time between disruption report and predicted hotspot, versus when a reactive system would notice | Model flags a hotspot before enough proxy demand signal exists to detect it reactively, shown in backtests |
| Prediction is honest about uncertainty | Every hotspot ships with a confidence range tied to how much historical data exists for that disruption type | 100% of outputs show a range, never a bare number |
| Simulation is demoable | The what-if scenario visibly updates the map as time steps forward | Live demo runs end-to-end without manual intervention |
| Business case is credible | Write-up names a believable paying fleet operator persona and reasons through willingness to pay | Reasoning is concrete (a named cost this saves or revenue this captures), not asserted |

Secondary goal: state plainly in the write-up that this predicts disruption-linked demand risk, not actual demand, since there's no access to real ride-level data to validate against, only proxy and check-in data.

## 3. Target Users

**Fleet dispatchers** at private-hire or minicab operators decide where to reposition idle drivers ahead of a predicted surge, using the hotspot map as their main working view.

**Drivers within a managed fleet** see the same hotspot map on a simple mobile view and can check in to confirm or correct what they're actually seeing at a location, feeding the verified-signal layer.

**Independent drivers outside a fleet** are a future, lower-priority audience for an individual subscription, not the initial paying customer, since Uber's own free heatmap makes that a harder sell on its own.

## 4. Data Sources

| Source | What it gives | Role | Access notes |
| --- | --- | --- | --- |
| TfL Unified API | Real-time and historical service disruptions, delays, closures, planned works | Core predictive signal | Public, free, well documented; disruption event schema also supports simulating a hypothetical event |
| Met Office weather data | Temperature, precipitation | Secondary signal, bad weather independently raises rideshare demand | Public API |
| London public event listings | Large concerts, matches, other big draws | Secondary signal, big events spike local demand | Public, varies by source |
| Historical TfL disruption logs | Past disruption events over time | Backtesting and validation | Public |

**Open gap, stated plainly rather than glossed over:** there's no access to Uber's or TfL's own real ride-demand data. That's the reason the driver check-in layer exists: verified check-ins are what fill the gap a statistical model alone cannot, the same design principle used throughout this project.

## 5. Core Features

**Fleet dispatcher app**

1. Live hotspot map ranked by predicted demand, factoring current disruption status across the service area.
2. Historical validation view, showing past predicted hotspots against what check-ins and proxy data actually showed.

**Driver check-in**

1. Confirm or correct actual demand and wait times at a location, feeding the verified-signal layer that overrides the statistical estimate wherever recent data exists.

**What-if disruption simulator (headline demo feature)**

1. Pick a line or station and a hypothetical disruption, then watch predicted demand ripple outward across nearby areas over 15, 30, and 60-minute steps.

**Shared system layer**

1. Confidence and recency display on every hotspot, dropping visibly for disruption types the model has little history on.
2. Plain-English summary per hotspot, translating the model's inputs into one sentence a dispatcher can act on immediately.

## 6. Technical Architecture

| Layer | Choice | Why |
| --- | --- | --- |
| Data pipeline | Python, pandas | Pulls and aligns TfL, weather, and event data on a common time-location grid |
| Simulation engine | A simple spatial-propagation model, demand influence decaying with distance and time from a disruption point | Lightweight, explainable, appropriate given limited disruption-to-outcome ground truth, not a deep model that would overfit a small dataset |
| Prediction validation | Backtested against historical disruptions using proxy demand signals, clearly labelled as proxy, not real ride data | Honest about the data gap named in Section 4 |
| Dispatcher frontend | Streamlit with a Folium or Plotly choropleth/map layer | Fast to build, good enough for a judged demo |
| Driver check-in form | A minimal mobile-friendly web page, separate from the dispatcher dashboard | Check-ins happen in the field on a phone; Streamlit suits desktop dispatcher viewing better than quick field entry |
| Check-in data store | Lightweight hosted backend, e.g. Supabase or Firebase | Needs write access from drivers in the field and near-real-time read access from the dispatcher view |
| Hosting | Streamlit Community Cloud | Free, fast to deploy for a judged demo |

## 7. Hackathon Fit

**EurekaDev 2026** (Coding Track, Economics category): the information-advantage framing gives it a real Economics hook, distinct from a crowded CS+AI category; the dispatcher app, check-in loop, and what-if simulator together satisfy "build a functional prototype"; citations to TfL's public API and the Grossman-Stiglitz information-value argument satisfy the citations expectation.

**Neighborhood Hacks and ForgeHacks:** not recommended for this project. This is a commercial B2B tool for a fleet business, not a community-impact story, so it doesn't honestly fit Neighborhood Hacks' brief; and it's a prediction-and-economics project first, AI second, so it would read as a weaker, force-fit entry in ForgeHacks' AI-only tracks. Better to submit this once, well, to EurekaDev than stretch it across events it doesn't naturally fit.

| Requirement | EurekaDev |
| --- | --- |
| Demo video | ≤ 4 min, problem + solution + how it works |
| Public GitHub repo | Required (Coding Track) |
| Written description | Problem, solution, tech used, challenges, next steps |
| Live demo | Recommended |

## 8. Milestones

| Phase | Focus | Exit criteria |
| --- | --- | --- |
| 1 | Pull and clean TfL, weather, and event data; confirm historical disruption logs go back far enough to backtest against | A clean dataset exists, even if the proxy demand signal is imperfect |
| 2 | Build the spatial-propagation simulation engine and validate it against historical disruptions | Model produces a plausible, honestly-labelled hotspot forecast with a confidence range |
| 3 | Build the dispatcher dashboard, hotspot map, and the what-if simulator's time-stepped animation | Dashboard runs end-to-end locally on real output |
| 4 | Add the driver check-in form and data store; talk to at least one real private-hire operator to pressure-test the business case | Full demo works without manual intervention; the write-up's business case reflects a real conversation, not just assertion |
| Final days | Record demo video, write submission text, deploy, submit | Submission complete well before the deadline |

Confirm the exact remaining time against EurekaDev's Oct 20 close before locking this plan in, and compress phases 3–4 rather than phase 1–2 if time is short, since a clean dataset and a validated model matter more to credibility than UI polish.

## 9. Risks & Mitigations

| Risk | Why it matters | Mitigation |
| --- | --- | --- |
| No access to real ride-demand data | Model's real-world accuracy can't be directly verified | State plainly this predicts disruption-linked demand risk, validated against proxy signals and check-ins, not real trip data |
| Uber's own heatmap dominates driver trust | Hard to get drivers or fleets to value a third-party signal | Position explicitly as a lead-time tool for fleets without Uber's infrastructure, never as a replacement for it |
| Simulation is a simplification | Pure distance-based propagation ignores real road and transit network structure | State this openly; weight by network structure only as a stretch goal, not a claimed feature |
| Small number of real disruption events to learn from | Same small-sample risk as earlier versions of this project | Use the longest available historical lookback; show confidence ranges honestly |
| Business case is asserted, not tested | May not reflect real fleet operator willingness to pay | Talk to at least one real private-hire operator before submission |
| Live demo failure | API calls failing during judging | Cache a working snapshot of all outputs as a fallback |

**Cut list if behind schedule:** drop network-weighted propagation first, since it was never core scope. If still behind, ship the what-if simulator as a single before/after snapshot instead of a multi-step animation, but keep the hotspot map and check-in loop, since those two are what make the business case real rather than theoretical.

## 10. Out of Scope / Future Work

- Real-time integration with an actual fleet's live GPS or dispatch system.
- An individual Uber-driver-facing product competing directly with Uber's built-in heatmap.
- Payment or billing infrastructure for the hackathon build itself.
- Expansion beyond London, would need each city's own open transit-disruption API.
- Road-network-weighted demand propagation, a refinement for later, not core scope now.
