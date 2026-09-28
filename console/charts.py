"""The backtest chart (DESIGN.md, Evidence tab 7C): one row per strategy, the mean excess bike
activity as a dot with its 90% interval, and every disruption as a faint dot so a small sample
looks like a small sample. SurgeSignal in the accent; the two common-sense rules in ink."""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go

INK, MUTED, RULE, ACCENT = "#14161A", "#8A8E95", "#E2E0D8", "#C8321E"
LABELS = {"engine": "SurgeSignal's top stations", "closed": "The closed stations", "busiest": "The area's busiest stations"}


def backtest_chart(bt: dict[str, Any], height: int = 280) -> go.Figure:
    rules = {r["rule"]: r for r in bt["rules"]}
    order = [r for r in ("busiest", "closed", "engine") if r in rules]  # engine on top
    fig = go.Figure()
    fig.add_vline(x=0, line={"color": MUTED, "width": 1})
    for r in order:
        colour = ACCENT if r == "engine" else INK
        y = LABELS[r]
        events = [e for e in bt["per_event"] if e.get(r) is not None]
        fig.add_trace(go.Scatter(
            x=[e[r] for e in events], y=[y] * len(events), mode="markers",
            marker={"size": 9, "color": "rgba(0,0,0,0)", "line": {"color": MUTED, "width": 1.5}},
            text=[e["label"] for e in events], hovertemplate="%{text}<br>%{x:+.1f} bike moves vs normal<extra></extra>",
            showlegend=False))
        s = rules[r]
        fig.add_trace(go.Scatter(
            x=[s["low"], s["high"]], y=[y, y], mode="lines", line={"color": colour, "width": 2},
            hoverinfo="skip", showlegend=False))
        fig.add_trace(go.Scatter(
            x=[s["mean"]], y=[y], mode="markers", marker={"size": 13, "color": colour, "line": {"color": "#FAFAF7", "width": 2}},
            hovertemplate=f"<b>{LABELS[r]}</b><br>mean %{{x:+.1f}}<br>90% interval {s['low']:+.1f} to {s['high']:+.1f}"
                          f"<br>n = {s['n']} disruptions<extra></extra>",
            showlegend=False))
    fig.update_layout(
        height=height, margin={"l": 0, "r": 16, "t": 8, "b": 0}, paper_bgcolor="#FAFAF7", plot_bgcolor="#FAFAF7",
        font={"family": "IBM Plex Sans, Helvetica Neue, Arial", "color": INK, "size": 14},
        xaxis={"title": {"text": "Extra bike moves per station in the 30 min after the alert, vs the same time on other days",
                         "font": {"size": 13, "color": "#5B5F66"}},
               "gridcolor": RULE, "zeroline": False, "ticks": ""},
        yaxis={"categoryorder": "array", "categoryarray": [LABELS[r] for r in order], "ticks": "", "showgrid": False},
        hoverlabel={"bgcolor": "#FFFFFF", "bordercolor": RULE, "font": {"color": INK}},
    )
    return fig
