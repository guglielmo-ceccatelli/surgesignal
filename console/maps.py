"""Ripple maps (DESIGN.md → Maps): the disrupted section in ink, demand circles on one shared
sequential scale, the area as a thin outline. Small multiples first (+8/+15/+30/+60 min side by
side, same zoom), and one larger explore map for a single step."""

from __future__ import annotations

import math
from collections.abc import Sequence

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from surgesignal.console import Ripple

INK, MUTED = "#14161A", "#8A8E95"
DEMAND_SCALE = [[0.0, "#FBEFE6"], [0.25, "#F4C7A8"], [0.5, "#E8895F"], [0.75, "#C8321E"], [1.0, "#7A1A0E"]]
STYLE = "carto-positron"


def _circle(lat: float, lon: float, km: float, n: int = 72) -> tuple[list[float], list[float]]:
    dlat = km / 111.32
    dlon = km / (111.32 * math.cos(math.radians(lat)))
    pts = [(lat + dlat * math.sin(2 * math.pi * i / n), lon + dlon * math.cos(2 * math.pi * i / n)) for i in range(n + 1)]
    return [p[0] for p in pts], [p[1] for p in pts]


def _zoom(km: float) -> float:
    return max(9.0, 12.6 - math.log2(max(km, 0.5)))


def _add_frame(fig: go.Figure, rp: Ripple, minutes: str, cmax: float, row: int, col: int, subplot: bool) -> None:
    kw = {"row": row, "col": col} if subplot else {}
    lat, lon = _circle(rp.area.lat, rp.area.lon, rp.area.radius_km)
    fig.add_trace(go.Scattermap(lat=lat, lon=lon, mode="lines", line={"width": 1, "color": MUTED},
                                hoverinfo="skip", showlegend=False), **kw)
    for path in rp.paths:
        fig.add_trace(go.Scattermap(lat=[s.lat for s in path], lon=[s.lon for s in path], mode="lines+markers",
                                    line={"width": 4, "color": INK}, marker={"size": 7, "color": INK},
                                    hoverinfo="text", text=[f"{s.name} (disrupted)" for s in path], showlegend=False), **kw)
    rows = [r for r in rp.frames if r["minutes"] == minutes]
    if rows:
        top = max(r["extra_riders"] for r in rows) or 1
        fig.add_trace(go.Scattermap(
            lat=[r["lat"] for r in rows], lon=[r["lon"] for r in rows], mode="markers",
            marker={"size": [8 + 22 * (r["extra_riders"] / top) ** 0.5 for r in rows], "color": [r["demand_pct"] for r in rows],
                    "coloraxis": "coloraxis", "opacity": 0.9},
            text=[f"<b>{r['station']}</b><br>+{r['low_pct']}–{r['high_pct']}% vs normal<br>{r['why']}" for r in rows],
            hoverinfo="text", showlegend=False), **kw)


def _layout(fig: go.Figure, rp: Ripple, cmax: float, height: int, n_maps: int) -> None:
    centre = {"lat": rp.area.lat, "lon": rp.area.lon}
    zoom = _zoom(rp.area.radius_km) - (0.6 if n_maps > 1 else 0)
    maps = {("map" if i == 0 else f"map{i + 1}"): {"style": STYLE, "center": centre, "zoom": zoom} for i in range(n_maps)}
    fig.update_layout(**maps, height=height, margin={"l": 0, "r": 0, "t": 28 if n_maps > 1 else 0, "b": 0},
                      paper_bgcolor="#FAFAF7", font={"family": "IBM Plex Sans, Helvetica Neue, Arial", "color": INK, "size": 13},
                      coloraxis={"colorscale": DEMAND_SCALE, "cmin": 0, "cmax": cmax,
                                 "colorbar": {"title": {"text": "Demand vs<br>normal (%)", "font": {"size": 12}},
                                              "thickness": 10, "len": 0.8, "outlinewidth": 0}})


# The colour scale never tops out below this, so +2% on minor delays reads pale, not as a crisis.
# 25% is about an unplanned suspension at its own stations.
CMAX_FLOOR = 25.0


def _cmax(rp: Ripple) -> float:
    return max(CMAX_FLOOR, max((r["demand_pct"] for r in rp.frames), default=0))


def small_multiples(rp: Ripple, steps: Sequence[str], height: int = 300) -> go.Figure:
    fig = make_subplots(rows=1, cols=len(steps), specs=[[{"type": "map"}] * len(steps)],
                        subplot_titles=[s.replace("+", "+ ") for s in steps], horizontal_spacing=0.008)
    cmax = _cmax(rp)
    for i, step in enumerate(steps):
        _add_frame(fig, rp, step, cmax, 1, i + 1, True)
    _layout(fig, rp, cmax, height, len(steps))
    fig.update_annotations(font={"size": 13, "color": INK})
    return fig


def single_map(rp: Ripple, step: str, height: int = 520) -> go.Figure:
    fig = go.Figure()
    cmax = _cmax(rp)
    _add_frame(fig, rp, step, cmax, 1, 1, False)
    _layout(fig, rp, cmax, height, 1)
    return fig
