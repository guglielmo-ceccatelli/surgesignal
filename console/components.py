"""HTML building blocks for the console, from DESIGN.md. Plain functions returning HTML strings
(rendered with st.html), so they're testable without Streamlit. All text is escaped."""

from __future__ import annotations

from html import escape
from typing import Sequence

from surgesignal.console import Action

CSS = """
<style>
:root{--bg:#FAFAF7;--surface:#F0EFEA;--ink:#14161A;--muted:#5B5F66;--rule:#E2E0D8;--accent:#C8321E;
--estimate:#6F5712;--estimate-bg:#F6EEDA;--evidence:#1F5E4A;--evidence-bg:#E3F0EA;--stale:#B42318;--stale-bg:#FDECEA;
--phone:#E7EBF0;--serif:'IBM Plex Serif',Georgia,serif;--sans:'IBM Plex Sans','Helvetica Neue',Arial,sans-serif}
.ss{font-family:var(--sans);color:var(--ink);font-variant-numeric:tabular-nums}
.ss-mark{font-family:var(--serif);font-weight:600;font-size:22px;letter-spacing:-.01em;display:flex;align-items:center;gap:8px}
.ss-mark i{width:10px;height:10px;border-radius:50%;background:var(--accent);display:inline-block}
.ss-h1{font-family:var(--serif);font-weight:600;font-size:40px;line-height:48px;margin:24px 0 8px;letter-spacing:-.015em}
.ss-lede{font-size:18px;line-height:28px;color:var(--muted);margin:0 0 24px}
.ss-lede b{color:var(--ink);font-weight:500}
.ss-strip{display:flex;flex-wrap:wrap;background:var(--surface);border-radius:6px;padding:16px 8px;margin:0 0 8px}
.ss-fig{flex:1 1 200px;padding:0 16px;border-left:1px solid var(--rule)}
.ss-fig:first-child{border-left:0}
.ss-num{font-size:26px;line-height:32px;font-weight:600}
.ss-lab{font-size:14px;line-height:20px;color:var(--muted);margin:2px 0 6px}
.ss-badge{display:inline-block;font-size:12px;line-height:16px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;padding:2px 6px;border-radius:4px}
.ss-badge.estimate{color:var(--estimate);background:var(--estimate-bg)}
.ss-badge.evidence{color:var(--evidence);background:var(--evidence-bg)}
.ss-badge.replay,.ss-badge.recorded{color:var(--ink);background:var(--surface);border:1px solid var(--rule)}
.ss-badge.live{color:var(--evidence);background:var(--evidence-bg)}
.ss-badge.stale{color:var(--stale);background:var(--stale-bg)}
.ss-note{font-size:14px;line-height:20px;color:var(--muted)}
.ss-banner{border-radius:6px;padding:12px 16px;font-size:16px;margin:8px 0 16px}
.ss-banner.stale{background:var(--stale-bg);color:var(--stale)}
.ss-banner.info{background:var(--surface);color:var(--ink)}
.ss-phone{width:300px;max-width:100%;margin:0 auto;border:10px solid var(--ink);border-radius:28px;background:var(--phone);
 box-shadow:0 12px 32px rgba(20,22,26,.12);padding:14px 10px 18px}
.ss-phone-top{font-size:12px;color:var(--muted);text-align:center;margin-bottom:10px}
.ss-bubble{background:#fff;border-radius:12px;padding:10px 12px;font-size:14px;line-height:20px;white-space:pre-wrap;word-wrap:break-word}
.ss-bubble-time{font-size:11px;color:var(--muted);text-align:right;margin-top:4px}
.ss-action{border:1px solid var(--rule);border-radius:6px;padding:16px;background:#fff}
.ss-action-big{font-size:32px;line-height:38px;font-weight:600}
.ss-action-big em{font-style:normal;color:var(--accent)}
.ss-action ul{margin:8px 0 8px 18px;padding:0;font-size:16px;line-height:24px}
.ss-steps{font-size:16px;line-height:24px;color:var(--muted);margin:0 0 16px}
.ss-steps b{color:var(--ink);font-weight:500}
.ss-steps span{color:var(--accent);padding:0 6px}
/* map credits stay visible (licence) but small */
.maplibregl-ctrl-attrib{font-size:10px!important;line-height:14px!important;padding:0 4px!important;background:rgba(250,250,247,.8)!important}
.maplibregl-ctrl-attrib-button{display:none!important}
</style>
"""


def badge(kind: str, text: str | None = None) -> str:
    kind = kind.lower()
    return f'<span class="ss-badge {escape(kind)}">{escape(text or kind)}</span>'


def wordmark() -> str:
    return '<div class="ss ss-mark"><i aria-hidden="true"></i>SurgeSignal</div>'


def hero(headline: str, lede: str) -> str:
    return f'<div class="ss"><div class="ss-h1">{escape(headline)}</div><p class="ss-lede">{lede}</p></div>'


def steps(parts: Sequence[str]) -> str:
    joined = '<span aria-hidden="true">→</span>'.join(f"<b>{escape(p)}</b>" for p in parts)
    return f'<p class="ss ss-steps">{joined}</p>'


def figure_strip(figures: Sequence[tuple[str, str, str]]) -> str:
    """figures: (number, label, badge kind)."""
    cells = "".join(f'<div class="ss-fig"><div class="ss-num">{escape(n)}</div><div class="ss-lab">{escape(label)}</div>'
                    f'{badge(kind)}</div>' for n, label, kind in figures)
    return f'<div class="ss ss-strip" role="list">{cells}</div>'


def phone_alert(text: str, time_label: str = "") -> str:
    stamp = f'<div class="ss-bubble-time">{escape(time_label)}</div>' if time_label else ""
    return (f'<div class="ss ss-phone" role="img" aria-label="Telegram alert: {escape(text)}">'
            f'<div class="ss-phone-top">SurgeSignal · Telegram</div>'
            f'<div class="ss-bubble">{escape(text)}{stamp}</div></div>')


def action_card(a: Action | None) -> str:
    if a is None:
        return '<div class="ss ss-action"><div class="ss-note">Nothing in this area is affected.</div></div>'
    if not a.alerting:
        return (f'<div class="ss ss-action"><div class="ss-action-big">No alert: hold position</div>'
                f'<div class="ss-note">+{a.pct_low}–{a.pct_high}% booking demand, too small to move cars for. '
                f'Why: {escape(a.why)}.</div></div>')
    if a.cars:
        big = f"Move <em>{a.cars}</em> of {a.idle} idle cars"
        items = "".join(f"<li>{escape(name)}: {n}</li>" for name, n in a.split)
    else:
        big = "Raise coverage nearby"
        items = "".join(f"<li>{escape(name)}</li>" for name in a.stations)
    return (f'<div class="ss ss-action"><div class="ss-action-big">{big}</div>'
            f'<div class="ss-note">+{a.pct_low}–{a.pct_high}% booking demand until at least ~{escape(a.until)}</div>'
            f'<ul>{items}</ul><div class="ss-note">Why: {escape(a.why)}. Wait nearby, not at the station: '
            f'private hire is pre-booked only.</div></div>')


def banner(kind: str, text: str) -> str:
    return f'<div class="ss ss-banner {escape(kind)}">{escape(text)}</div>'
