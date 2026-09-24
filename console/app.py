"""SurgeSignal console (Streamlit). Design: DESIGN.md; spec: docs/designs/surgesignal.md →
"Console redesign for EurekaDev".

    streamlit run console/app.py

Overview (a real recorded disruption) · Try a disruption (presets, ?scenario=) · The economics ·
How it works · Evidence · Live. Works deployed with no local data: Overview, presets, economics
and method come from committed files; Live and Evidence fall back to labelled recordings.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # Streamlit runs this file as a script

from console.components import CSS, action_card, badge, banner, figure_strip, hero, phone_alert, steps, wordmark  # noqa: E402
from console.maps import single_map, small_multiples  # noqa: E402
from surgesignal.alerts.format import LONDON  # noqa: E402
from surgesignal.console import (  # noqa: E402
    PRESETS,
    RAIN,
    REPLAYS_DIR,
    RIPPLE_STEPS,
    live_view,
    ordered_stations,
    params_rows,
    qualifying_incidents,
    replay_story,
    run_preset,
    station_by_name,
    whatif,
)
from surgesignal.economics import DEFAULTS, EconInputs, evaluate  # noqa: E402
from surgesignal.engine.explain import SEVERITY_WORDS, line_label  # noqa: E402
from surgesignal.engine.params import load_params  # noqa: E402
from surgesignal.engine.types import ServiceArea  # noqa: E402
from surgesignal.inputs.baseline import Baseline  # noqa: E402
from surgesignal.store.state import FileStore  # noqa: E402
from surgesignal.tfl.network import load_network  # noqa: E402

DATA = Path(os.environ.get("SURGESIGNAL_DATA", REPO / "data"))  # override: tests render the public copy (no data)
STATE_DIR, STATUS_DIR, HEARTBEAT = DATA / "state", DATA / "raw" / "status", DATA / "alerter_heartbeat.json"
EVIDENCE_FILE = REPO / "surgesignal" / "data" / "evidence.json"
REPLAY = ("district-2026-09-23", "district", "Putney Bridge", 5.0, 6)
STEPS = [f"+{m} min" for m in RIPPLE_STEPS]
BACKTEST_TARGET = 20
# No scroll-wheel zoom: scrolling the page over a map must scroll the page, not zoom the map.
MAP_CONFIG = {"displayModeBar": False, "scrollZoom": False}

st.set_page_config(page_title="SurgeSignal", page_icon=":material/radio_button_checked:", layout="wide")
st.html(CSS)


@st.cache_resource(show_spinner="Loading the Tube network…")
def resources():
    return load_network(), load_params(), Baseline.load()


network, params, baseline = resources()


@st.cache_resource(show_spinner="Replaying 23 September…")
def story():
    folder, line, centre, km, idle = REPLAY
    c = station_by_name(network, centre)
    return replay_story(network, params, baseline, REPLAYS_DIR / folder, line, centre, ServiceArea(c.lat, c.lon, km), idle)


@st.cache_resource
def preset_ripple(slug: str):
    return run_preset(network, params, baseline, PRESETS[slug])


@st.cache_data(show_spinner=False, max_entries=64)
def custom_ripple(line_id, a, b, severity, unplanned, start_iso, rain, centre, radius, idle):
    c = station_by_name(network, centre)
    return whatif(network, params, baseline, line_id, a, b, severity, unplanned, datetime.fromisoformat(start_iso),
                  rain, ServiceArea(c.lat, c.lon, radius), idle)


@st.cache_data(ttl=600, show_spinner=False)
def logged_count() -> tuple[int, str]:
    if STATUS_DIR.exists():
        return qualifying_incidents(STATUS_DIR, network, params), "counted live from this machine's logs"
    if EVIDENCE_FILE.exists():
        e = json.loads(EVIDENCE_FILE.read_text())
        return int(e["qualifying"]), f"as of {e['as_of']}, from the logging machine"
    return 0, "no logs on this server"


def ripple_block(rp, key: str, alert_time: str = "") -> None:
    """Four small maps, caption, then explore map beside the phone and action card."""
    st.plotly_chart(small_multiples(rp, STEPS), width="stretch", config=MAP_CONFIG, key=f"sm-{key}")
    st.html(f'<p class="ss ss-note">{rp.caption}</p>')
    left, right = st.columns([3, 2], gap="large")
    with left:
        step = st.segmented_control("Explore one moment", STEPS, default=STEPS[0], key=f"step-{key}")
        st.plotly_chart(single_map(rp, step or STEPS[0], height=440), width="stretch",
                        config=MAP_CONFIG, key=f"one-{key}")
    with right:
        if rp.action is None or rp.action.alerting:
            st.html(phone_alert(rp.alert, alert_time))
        else:  # the bot stays silent below the threshold, so don't show a message it wouldn't send
            st.html(banner("info", "No Telegram message: the rise is below the alert threshold. Planned closures "
                                   "and minor delays give riders time to re-route."))
        st.html(action_card(rp.action))


# ---- header ----------------------------------------------------------------------------

st.html(wordmark())
st.html(hero("When the Tube fails, Uber surges. Be there first, at a fixed fare.",
             "SurgeSignal reads TfL's live line status and tells a minicab dispatcher <b>where the extra bookings "
             "will come from, and how many cars to move</b>, before they show up in the firm's own calls."))
st.html(steps(["TfL reports a disruption", "SurgeSignal forecasts where bookings form", "you move cars before the surge"]))
st.html(figure_strip([
    ("1.5–2.5×", "what Uber charges during Tube disruptions (reported)", "evidence"),
    ("Fixed", "what a minicab firm charges: riders priced out by the surge are winnable", "estimate"),
    ("~5 vs ~20 min", "pickup time with cars placed early vs dispatching after the calls", "estimate"),
]))
st.caption("Predicts disruption-linked demand risk relative to a normal day, not trip counts: London has no public "
           "ride-level data. EVIDENCE has a source; ESTIMATE is an assumption shown so you can judge it.")

# Lazy tabs: only the open tab runs, so the page never holds more than one tab's maps (WebGL
# contexts are limited per page). A ?scenario= link opens straight onto Try a disruption.
TABS = ["Overview", "Try a disruption", "The economics", "How it works", "Evidence", "Live"]
overview, trial, econ, how, evidence, live = st.tabs(
    TABS, key="tab", on_change="rerun", default="Try a disruption" if "scenario" in st.query_params else "Overview")

# ---- Overview: a real recorded disruption ------------------------------------------------

with overview:
    if overview.open:
        try:
            s = story()
        except FileNotFoundError:
            st.html(banner("info", "The recorded replay isn't available here: showing a preset scenario instead."))
            ripple_block(preset_ripple("district-evening"), "overview")
        else:
            st.html(badge("replay", f"Replay · {s.title} · recorded {s.recorded}"))
            first = next((t for t in s.sent if t[1] == "new"), None)
            st.subheader("A real evening, replayed through SurgeSignal")
            st.markdown(f"At **{s.started_london}** on 23 September TfL reported: *{s.tfl_reason.split('.')[0]}.* "
                        f"SurgeSignal's alert for a dispatcher near **{s.area_label}** went out at **{first[0] if first else '—'}**. "
                        "Below: where it expected the extra bookings, 8 to 60 minutes in.")
            if s.ripple:
                ripple_block(s.ripple, "overview", first[0] if first else "")
            with st.expander(f"Everything SurgeSignal sent that evening ({len(s.sent)} messages; edits don't buzz the phone)"):
                kinds = {"new": "new alert", "edit": "edit (silent)", "resolved": "all-clear"}
                st.dataframe(pd.DataFrame([{"time": t, "type": kinds[k], "message": text.splitlines()[0]} for t, k, text in s.sent]),
                             hide_index=True, width="stretch")
            st.markdown("**Try your own disruption** in the next tab: pick a preset or build one.")

# ---- Try a disruption ------------------------------------------------------------------

with trial:
    if trial.open:
        labels = {p.label: slug for slug, p in PRESETS.items()}
        wanted = st.query_params.get("scenario")
        default = "Custom" if wanted == "custom" else next((lab for lab, slug in labels.items() if slug == wanted), None)
        choice = st.pills("Scenario", list(labels) + ["Custom"], default=default or next(iter(labels)), key="scenario")
        choice = choice or next(iter(labels))
        st.query_params["scenario"] = labels.get(choice, "custom")

        if choice != "Custom":
            p = PRESETS[labels[choice]]
            st.html(f'<p class="ss ss-note">{badge("estimate", "Scenario")} {line_label(network.lines[p.line].name)} '
                    f'{SEVERITY_WORDS[p.severity]} between {p.a} and {p.b}, {p.start.astimezone(LONDON):%a %d %b %H:%M}, '
                    f'{p.rain.lower()}; dispatcher at {p.centre}, {p.radius_km:g} km, {p.idle} idle cars. '
                    'Choose "Custom" to change anything.</p>')
            ripple_block(preset_ripple(labels[choice]), f"preset-{labels[choice]}")
        else:
            with st.expander("Customise", expanded=True):
                c1, c2, c3 = st.columns(3)
                line_ids = sorted(network.lines, key=lambda lid: network.lines[lid].name)
                line_id = c1.selectbox("Line", line_ids, index=line_ids.index("northern"),
                                       format_func=lambda lid: line_label(network.lines[lid].name))
                stops = ordered_stations(network, line_id)
                names = [s.name for s in stops]
                a = c2.selectbox("From", range(len(stops)), index=names.index("Stockwell") if "Stockwell" in names else 0,
                                 format_func=lambda i: names[i])
                b = c3.selectbox("To", range(len(stops)), index=names.index("Morden") if "Morden" in names else len(stops) - 1,
                                 format_func=lambda i: names[i])
                c4, c5, c6, c7 = st.columns(4)
                severity = c4.selectbox("Severity", ("suspended", "part_suspended", "severe", "minor"),
                                        format_func=lambda s: SEVERITY_WORDS[s])
                unplanned = c5.toggle("Unplanned", value=True)
                rain = c6.selectbox("Weather", list(RAIN))
                day = c7.date_input("Day", date(2026, 9, 25))
                c8, c9, c10 = st.columns(3)
                start_local = c8.time_input("Starts at (London)", time(18, 0), step=900)
                all_names = sorted({s.name for s in network.stations.values()})
                centre = c9.selectbox("Dispatcher area centre", all_names, index=all_names.index("Clapham Common"))
                radius = c10.slider("Radius (km)", 1.0, 15.0, 5.0, 0.5)
                idle = st.number_input("Idle cars usually free", 0, 500, 6)
            start = datetime.combine(day, start_local, tzinfo=LONDON).astimezone(timezone.utc)
            try:
                rp = custom_ripple(line_id, stops[a].id, stops[b].id, severity, unplanned, start.isoformat(), rain,
                                   centre, radius, int(idle))
            except ValueError:
                st.html(banner("info", "Pick two stations on one route of this line: the From and To lists include every branch."))
            else:
                if not rp.frames:
                    st.html(banner("info", f"This disruption doesn't reach {centre} ({radius:g} km). Choose a centre nearer the line."))
                else:
                    ripple_block(rp, "custom")

# ---- The economics ---------------------------------------------------------------------

with econ:
    if econ.open:
        st.subheader("Why early information is worth money")
        st.markdown(
            "During a Tube disruption, demand for cars jumps near the affected stations. **Uber's surge pricing** rations it: "
            "fares rise 1.5–2.5×. A minicab firm charges a **fixed fare**, so every rider priced out by the surge is a customer it "
            "could win, but only if a car is close enough to quote a short pickup. The firm that knows first, positions first.\n\n"
            "That is the Grossman–Stiglitz point (1980): information is costly to obtain, so whoever acts on it first earns the "
            "return. SurgeSignal turns a free public signal, TfL's line status, into an action minutes before the demand appears "
            "in the firm's own bookings.")
        st.subheader("What it could be worth to a firm")
        st.caption("Change any input. Defaults marked ESTIMATE are placeholders until a real operator's figures replace them.")
        vals = {}
        cols = st.columns(2, gap="large")
        for i, (key, inp) in enumerate(DEFAULTS.items()):
            with cols[i % 2]:
                vals[key] = st.number_input(f"{inp.label} ({inp.unit})" if inp.unit else inp.label, min_value=0.0,
                                            value=float(inp.value), step=0.1 if key.startswith("surge") else 1.0, key=f"econ-{key}")
                st.html(f'<p class="ss ss-note">{badge(inp.kind.lower())} {inp.source}</p>')
        try:
            r = evaluate(EconInputs(**vals))
        except ValueError as exc:
            st.html(banner("info", f"{exc}."))
        else:
            st.html(figure_strip([
                (f"£{r.weekly_revenue:,.0f}", f"extra revenue a week ({r.jobs_per_week:g} jobs)", "estimate"),
                (f"£{r.yearly_revenue:,.0f}", "a year at that rate", "estimate"),
                (f"£{r.rider_saving_low:,.2f}–{r.rider_saving_high:,.2f}", "a rider saves per trip by choosing you over surging Uber", "estimate"),
                (f"{r.pickup_gain_min:g} min", "faster pickup, which is what lets you take the job", "estimate"),
            ]))

# ---- How it works ----------------------------------------------------------------------

with how:
    if how.open:
        st.subheader("Signal → ripple → action")
        st.markdown(
            "1. **Signal.** Every minute SurgeSignal reads TfL's line status and turns TfL's text (\"no service between "
            "Stockwell and Morden\") into the exact stations affected, following branches and routing hints.\n"
            "2. **Ripple.** Riders spill out of the affected stations and look for cars. Demand is highest in the disrupted "
            "section and fades with distance; the affected area **widens the longer the disruption lasts**. Rain and big "
            "events amplify it. Busy stations at busy times gain the most riders.\n"
            "3. **Action.** The top stations become one message: how many idle cars to move and where, for how long, and why. "
            "It's a forecast: scored 8 minutes ahead, where demand is heading.")
        with st.expander("The model, in formulas"):
            st.markdown(r"""
    $$\text{uplift}(s,t) = \sum_{d} \text{severity}(d)\cdot \text{planned}(d)\cdot e^{-\text{dist}(s,d)/\lambda(d,t)}\cdot g(d,t)
    \qquad \lambda(d,t) = \lambda_0\,\big(1 + \text{growth}\cdot\min(\tfrac{\text{minutes active}}{60}, 1)\big)$$

    $$\text{demand vs normal} = (1+\text{uplift})\times\text{weather}\times\text{events} - 1
    \qquad \text{riders added} = \text{busyness}(s,t)\times\text{uplift}\times\text{weather}\times\text{events}$$

    $g$ ramps up over 8 minutes, holds while the disruption lasts and halves every 15 minutes after it ends. Ranges: low = severity ×0.5
    and $\lambda$ ×0.5; high = ×1.5 each. Rain amplifies a disruption but never creates a hotspot on its own.""")
        st.subheader("Every number, and where it comes from")
        st.dataframe(pd.DataFrame(params_rows()), hide_index=True, width="stretch",
                     column_config={"parameter": "Parameter", "value": "Value", "unit": "Unit",
                                    "kind": st.column_config.TextColumn("Kind", help="EVIDENCE has a source; ESTIMATE is an assumption"),
                                    "note": st.column_config.TextColumn("Why", width="large")})
        report = baseline.data.get("report", {})
        sizes, fallbacks = report.get("size_sources", {}), report.get("shape_fallbacks", {})
        st.subheader("Data")
        st.markdown(
            f"* **Line status and disruptions:** TfL Unified API, every minute.\n"
            f"* **Station busyness:** TfL annual station counts 2023 ({sizes.get('counts', '?')} stations counted, "
            f"{sizes.get('same-place', '?')} borrowed from the Tube station at the same place, {sizes.get('median', '?')} median) × "
            f"TfL 15-minute crowding profiles, blended half-and-half with London's typical day "
            f"({fallbacks.get('implausible', '?')} implausible station-days replaced).\n"
            "* **Weather:** Open-Meteo. **Events:** a hand-kept fixture list. **Bike docks** (the validation proxy): TfL BikePoint.")
        st.subheader("What it can't do")
        st.markdown("* It predicts demand **risk**, not trips: no public ride data exists to learn from or test against.\n"
                    "* Distance is straight-line, not along the network.\n"
                    "* Every ESTIMATE is a starting value, to be tuned by the backtest and operator feedback.")
        st.caption("Contains public sector information licensed under the Open Government Licence v2.0 (Transport for London). "
                   "Weather data by Open-Meteo. SurgeSignal is not affiliated with or endorsed by TfL.")

# ---- Evidence --------------------------------------------------------------------------

with evidence:
    if evidence.open:
        n, where = logged_count()
        st.subheader("Does it beat common sense?")
        st.markdown("The backtest compares SurgeSignal's top 3 stations with two simple rules, **\"the closed stations\"** and "
                    "**\"the busiest stations\"**, measured on where Santander bikes actually moved after each disruption, "
                    "against the same stations at normal times. It is reported whichever way it comes out.")
        st.progress(min(n / BACKTEST_TARGET, 1.0), text=f"{n} of {BACKTEST_TARGET} qualifying disruptions logged ({where})")
        st.caption("Data is collected every 5 minutes in the cloud (TfL status, disruptions, bike docks per station), "
                   "independent of any one machine. Results appear here once the target is reached.")
        if STATE_DIR.exists():
            checkins = FileStore(STATE_DIR).checkins_since(datetime.now(timezone.utc) - timedelta(days=30))
            st.subheader("Driver check-ins")
            if checkins:
                st.dataframe(pd.DataFrame([{"station": network.stations[c.station_id].name if c.station_id in network.stations
                                            else c.station_id, "reported": c.status, "time": f"{c.ts.astimezone(LONDON):%d %b %H:%M}"}
                                           for c in checkins]), hide_index=True, width="stretch")
            else:
                st.caption("None yet: check-ins arrive from drivers during a live trial.")

# ---- Live ------------------------------------------------------------------------------

with live:
    if live.open:
        now = datetime.now(timezone.utc)
        if not STATE_DIR.exists():
            s = story()
            st.html(badge("recorded", f"Recorded · {s.title} · 23 Sep"))
            st.html(banner("info", "The live feed runs on the dispatcher's own machine. This public copy shows the recorded "
                                   "23 September evening instead."))
            if s.ripple:
                st.html(action_card(s.ripple.action))
        else:
            try:
                view = live_view(FileStore(STATE_DIR), network, params, baseline, now, HEARTBEAT)
            except Exception:  # never a traceback: fall back to the recording
                st.html(banner("stale", "Live feed unavailable right now: showing the recorded 23 September evening."))
                view = None
            if view is not None:
                if view.checked_at is None:
                    st.html(banner("stale", "The alerter hasn't reported yet: live data may be missing."))
                elif view.stale:
                    age = int((now - view.checked_at).total_seconds() // 60)
                    st.html(banner("stale", f"TfL last checked {age} min ago: the alerter may have stopped."))
                else:
                    secs = int((now - view.checked_at).total_seconds())
                    st.html(badge("live", f"Live · TfL checked {secs} s ago"))
                area = f"{view.settings.area_label}, {view.settings.radius_km:g} km" if view.settings.has_area else "not set (/area in Telegram)"
                st.caption(f"Alert area: {area}")
                if not view.disruptions:
                    last = view.recent[0] if view.recent else None
                    tail = f" Last: {last['line']} {last['severity']}, ended {last['ended'].astimezone(LONDON):%H:%M}." if last else ""
                    st.html(banner("info", f"Quiet now: no Tube or Elizabeth line disruptions.{tail} "
                                           "The Overview replays a real evening."))
                else:
                    for d in view.disruptions:
                        where = ("whole line: TfL named no section, so it isn't mapped" if d.line_wide
                                 else f"{len(d.affected_station_ids)} stations")
                        st.write(f"• **{line_label(d.line_name)}** {SEVERITY_WORDS[d.severity_class]} "
                                 f"({'unplanned' if d.is_unplanned else 'planned'}, since {d.t0.astimezone(LONDON):%H:%M}; {where})")
                    if view.ripple and view.ripple.frames:
                        left, right = st.columns([3, 2], gap="large")
                        left.plotly_chart(single_map(view.ripple, view.ripple.frames[0]["minutes"], height=440),
                                          width="stretch", config=MAP_CONFIG, key="live-map")
                        right.html(action_card(view.ripple.action))
                    elif view.settings.has_area:
                        st.caption("None of the mapped disruptions reaches your alert area.")
                if view.checkins:
                    st.subheader("Check-ins, last 30 min")
                    st.dataframe(pd.DataFrame([{"station": network.stations[c.station_id].name, "reported": c.status,
                                                "time": f"{c.ts.astimezone(LONDON):%H:%M}"} for c in view.checkins]),
                                 hide_index=True)
