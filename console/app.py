"""SurgeSignal dispatcher console (Streamlit).

    streamlit run console/app.py

Tabs: Live (the alerter's current view of your area), What-if (the demand ripple from any
disruption you pick, plus the exact alert it would send), How it works (formula, every number
and its source), Backtest (after two weeks of logs). All logic lives in surgesignal/console.py.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # Streamlit runs this file as a script, not as part of the package

from surgesignal.alerts.format import LONDON  # noqa: E402
from surgesignal.alerts.settings import Settings  # noqa: E402
from surgesignal.console import RAIN, live_view, ordered_stations, params_rows, whatif  # noqa: E402
from surgesignal.engine.explain import SEVERITY_WORDS, line_label  # noqa: E402
from surgesignal.engine.params import load_params  # noqa: E402
from surgesignal.engine.types import ServiceArea  # noqa: E402
from surgesignal.inputs.baseline import Baseline  # noqa: E402
from surgesignal.store.state import FileStore  # noqa: E402
from surgesignal.tfl.network import load_network  # noqa: E402

STATE_DIR = REPO / "data" / "state"
SEVERITIES = ("suspended", "part_suspended", "severe", "minor")

st.set_page_config(page_title="SurgeSignal", page_icon="🚕", layout="wide")


@st.cache_resource
def resources():
    return load_network(), load_params(), Baseline.load()


network, params, baseline = resources()
names = sorted({s.name for s in network.stations.values()})


def station_by_name(name: str):
    return sorted((s for s in network.stations.values() if s.name == name), key=lambda s: s.id)[0]


def demand_map(df: pd.DataFrame, area: ServiceArea, animate: bool):
    fig = px.scatter_map(
        df, lat="lat", lon="lon", color="demand_pct", size="extra_riders", size_max=28,
        hover_name="station", hover_data={"demand_pct": True, "why": True, "lat": False, "lon": False, "extra_riders": False},
        animation_frame="minutes" if animate else None, color_continuous_scale="OrRd", range_color=(0, max(df["demand_pct"].max(), 1)),
        center={"lat": area.lat, "lon": area.lon}, zoom=11.5 - area.radius_km / 6, height=560,
        labels={"demand_pct": "Demand vs normal (%)"},
    )
    fig.update_layout(map_style="carto-positron", margin={"l": 0, "r": 0, "t": 0, "b": 0})
    return fig


# ---- sidebar: the dispatcher's area ----------------------------------------------------

store = FileStore(STATE_DIR) if STATE_DIR.exists() else None
saved = Settings.from_dict(store.get_state("settings")) if store else None

st.sidebar.header("Simulator area")
default_station = saved.area_label if saved and saved.area_label in names else "Clapham Common"
centre = st.sidebar.selectbox("Centre", names, index=names.index(default_station))
radius = st.sidebar.slider("Radius (km)", 1.0, 15.0, float(saved.radius_km) if saved and saved.has_area else 5.0, 0.5)
idle = st.sidebar.number_input("Idle cars usually free", 0, 500, int(saved.idle_drivers or 6) if saved else 6)
c = station_by_name(centre)
area = ServiceArea(c.lat, c.lon, radius)
st.sidebar.caption("Used by the What-if simulator. Live alerts use the area you set in Telegram with /area.")

st.title("🚕 SurgeSignal")
st.markdown("When a Tube or Elizabeth line disruption hits, Uber surges. SurgeSignal tells a fixed-fare minicab "
            "dispatcher **where the extra bookings will come from, before they arrive**, so cars are already nearby.")
st.caption("Predicts disruption-linked demand *risk* relative to a normal day, not trips: there is no public ride data "
           "for London. Every number is a range with its reason; every parameter is labelled evidence or assumption.")

live_tab, whatif_tab, how_tab, backtest_tab = st.tabs(["Live", "What-if simulator", "How it works", "Backtest"])

# ---- Live ------------------------------------------------------------------------------

with live_tab:
    if store is None:
        st.info("The live view reads the alerter's state on the machine running it. Run the alerter "
                "(`python -m workers.alerter`) or open the What-if simulator.")
    else:
        now = datetime.now(timezone.utc)
        view = live_view(store, network, params, baseline, now)
        if view.settings.has_area:
            st.caption(f"Alert area: **{view.settings.area_label}**, {view.settings.radius_km:g} km "
                       f"(change it in Telegram with /area). Updated {now.astimezone(LONDON):%H:%M}.")
        else:
            st.warning("No alert area set yet: send /area to the bot on Telegram.")
        if not view.disruptions:
            st.success("No Tube or Elizabeth line disruptions right now.")
        else:
            st.subheader("Disruptions now")
            for d in view.disruptions:
                where = "whole line: TfL named no section, so it isn't mapped" if d.line_wide else f"{len(d.affected_station_ids)} stations"
                st.write(f"• **{line_label(d.line_name)}** {SEVERITY_WORDS[d.severity_class]} "
                         f"({'unplanned' if d.is_unplanned else 'planned'}, since {d.t0.astimezone(LONDON):%H:%M}; {where})")
        if view.hotspots:
            st.subheader(f"Where demand is heading near {view.settings.area_label}")
            df = pd.DataFrame(view.hotspots)
            st.plotly_chart(demand_map(df, ServiceArea(view.settings.area_lat, view.settings.area_lon, view.settings.radius_km), False),
                            use_container_width=True)
            st.dataframe(df[["station", "demand", "why"]], hide_index=True, use_container_width=True)
        elif any(not d.line_wide for d in view.disruptions):
            st.info("None of the mapped disruptions reaches your alert area.")
        if view.checkins:
            st.subheader("Driver check-ins (last 30 min)")
            st.dataframe(pd.DataFrame([{"station": network.stations[c.station_id].name if c.station_id in network.stations
                                        else c.station_id, "status": c.status, "time": f"{c.ts.astimezone(LONDON):%H:%M}"}
                                       for c in view.checkins]), hide_index=True)

# ---- What-if -----------------------------------------------------------------------------

with whatif_tab:
    st.markdown("Pick a disruption and watch predicted demand **ripple outward** as it drags on. "
                "It runs the exact engine the live alerts use.")
    col1, col2, col3 = st.columns(3)
    line_ids = sorted(network.lines, key=lambda lid: network.lines[lid].name)
    line_id = col1.selectbox("Line", line_ids, index=line_ids.index("northern"),
                             format_func=lambda lid: line_label(network.lines[lid].name))
    stops = ordered_stations(network, line_id)
    stop_names = [s.name for s in stops]
    default_a = stop_names.index("Stockwell") if "Stockwell" in stop_names else 0
    default_b = stop_names.index("Morden") if "Morden" in stop_names else len(stops) - 1
    a = col2.selectbox("From", range(len(stops)), index=default_a, format_func=lambda i: stop_names[i])
    b = col3.selectbox("To", range(len(stops)), index=default_b, format_func=lambda i: stop_names[i])
    col4, col5, col6, col7 = st.columns(4)
    severity = col4.selectbox("Severity", SEVERITIES, format_func=lambda s: SEVERITY_WORDS[s])
    unplanned = col5.toggle("Unplanned", value=True)
    rain = col6.selectbox("Weather", list(RAIN))
    next_friday = date.today() + timedelta(days=(4 - date.today().weekday()) % 7)
    day = col7.date_input("Day", next_friday)
    start_local = st.slider("Starts at (London time)", value=time(18, 0), step=timedelta(minutes=15))
    start = datetime.combine(day, start_local, tzinfo=LONDON).astimezone(timezone.utc)
    try:
        result = whatif(network, params, baseline, line_id, stops[a].id, stops[b].id, severity, unplanned,
                        start, rain, area, int(idle))
    except ValueError as exc:
        st.warning(f"Can't simulate that: {exc}.")
    else:
        if not result.frames:
            st.info("That disruption doesn't reach your area. Move the centre or widen the radius in the sidebar.")
        else:
            st.caption("Press ▶ under the map to watch the ripple: +8 min (demand has built), then +15, +30 and +60 min.")
            st.plotly_chart(demand_map(pd.DataFrame(result.frames), area, True), use_container_width=True)
            left, right = st.columns([3, 2])
            left.markdown("**The alert your dispatcher would get**")
            left.code(result.alert, language=None, wrap_lines=True)
            first = pd.DataFrame([r for r in result.frames if r["minutes"] == result.frames[0]["minutes"]])
            top = first.sort_values("extra_riders", ascending=False).drop_duplicates("station").head(8)
            right.markdown("**Stations gaining the most riders**")
            right.dataframe(top.assign(demand=top["low_pct"].astype(str) + "–" + top["high_pct"].astype(str) + "%")[["station", "demand"]],
                            hide_index=True, use_container_width=True)

# ---- How it works ----------------------------------------------------------------------

with how_tab:
    st.subheader("The model")
    st.markdown(r"""
For every station $s$ in your area, at time $t$:

$$\text{uplift}(s,t) = \sum_{d} \text{severity}(d)\cdot \text{planned}(d)\cdot e^{-\text{dist}(s,d)/\lambda(d,t)}\cdot g(d,t)$$

$$\lambda(d,t) = \lambda_0\,(1 + \text{growth}\cdot\min(\tfrac{\text{minutes active}}{60}, 1))\qquad\text{(the ripple widens as a disruption drags on)}$$

$$\text{demand vs normal} = (1+\text{uplift})\times\text{weather}\times\text{events} - 1$$

$$\text{riders added} = \text{busyness}(s,t)\times\text{uplift}\times\text{weather}\times\text{events}\qquad\text{(ranks stations, sizes the car suggestion)}$$

* **dist** is the distance to the nearest station in the disrupted section; $g$ ramps up over 8 minutes, holds while the disruption lasts and halves every 15 minutes after it ends.
* **Ranges**: low = severity ×0.5 and $\lambda$ ×0.5; high = ×1.5 each.
* **Rain amplifies** a disruption's effect but never creates a hotspot on its own.
* **Alerts are forecasts**: scored 8 minutes ahead, where demand is heading.
""")
    st.subheader("Every number, and where it comes from")
    st.dataframe(pd.DataFrame(params_rows()), hide_index=True, use_container_width=True)
    st.subheader("Station busyness")
    report = baseline.data.get("report", {})
    st.markdown(
        f"*Size*: TfL annual station counts 2023 (typical daily entries + exits) relative to the median station — "
        f"{report.get('size_sources', {}).get('counts', '?')} stations counted directly, "
        f"{report.get('size_sources', {}).get('same-place', '?')} borrowed from the Tube station at the same place, "
        f"{report.get('size_sources', {}).get('median', '?')} given the median. *Shape*: TfL 15-minute crowding profiles, "
        "blended half-and-half with London's typical day; the typical day alone where a station's profile is implausible "
        f"({report.get('shape_fallbacks', {}).get('implausible', '?')} station-days) or missing.")
    st.subheader("What it can't do")
    st.markdown("* No ride-level data exists publicly, so this predicts demand **risk**, validated against proxies "
                "(bike-dock movement) and driver check-ins, not trips.\n"
                "* Distance is straight-line, not along the network (planned improvement).\n"
                "* Every parameter marked *assumption* is a starting value to be tuned by the backtest and operator feedback.")
    st.caption("Contains public sector information licensed under the Open Government Licence v2.0 (Transport for London). "
               "Weather: Open-Meteo.")

# ---- Backtest --------------------------------------------------------------------------

with backtest_tab:
    st.info("Coming once two weeks of disruptions have been logged (task T11): SurgeSignal's top 3 stations against two "
            "simple rules — \"the closed stations\" and \"the busiest stations\" — measured on bike-dock movement, "
            "reported whichever way it comes out.")
