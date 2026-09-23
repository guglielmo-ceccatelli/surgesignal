"""Turn TfL Line/Status JSON into incidents with explicit station sets.

    lineStatus ─► statusSeverity code ─► severity class (or ignore: Good Service, Service Closed…)
       │
       ├─ disruption.affectedStops non-empty? ─► those stations
       ├─ reason has "between A and B" clause(s)? ─► one incident per clause:
       │     A, B may be "X / Y" alternatives and carry "via Z" hints
       │     resolve names on THIS line ─► expand along each route containing both ends
       │       (shortest slice wins; "via Z" keeps only routes through Z)
       │     unresolvable ─► whole line, severity ×0.5, area_uncertain, problem logged  (5A)
       ├─ reason names single closed stations ("not stopping at X") ─► those stations
       └─ nothing locatable ─► line_wide (engine never ranks these)                   (OV#2)

Incident key = line + section (not severity), so a minor→severe escalation of the same
section is an UPDATE of one incident, while a new section is a new incident.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from surgesignal.tfl.names import normalise
from surgesignal.tfl.network import Network

# TfL statusSeverity code -> (severity class, planned by default). None = not a disruption.
# The class for each code is an assumption; see docs/designs/surgesignal.md (OV#2).
SEVERITY_BY_CODE: dict[int, tuple[str, bool] | None] = {
    0: ("minor", False),  # Special Service
    1: ("suspended", False),  # Closed
    2: ("suspended", False),  # Suspended
    3: ("part_suspended", False),  # Part Suspended
    4: ("suspended", True),  # Planned Closure
    5: ("part_suspended", True),  # Part Closure
    6: ("severe", False),  # Severe Delays
    7: ("minor", False),  # Reduced Service
    8: ("part_suspended", False),  # Bus Service (replacement buses)
    9: ("minor", False),  # Minor Delays
    10: None,  # Good Service
    11: ("part_suspended", True),  # Part Closed
    12: None,  # Exist Only
    13: None,  # No Step Free Access
    14: None,  # Change of frequency
    15: ("minor", False),  # Diverted
    16: ("suspended", False),  # Not Running
    17: ("minor", False),  # Issues Reported
    18: None,  # No Issues
    19: None,  # Information
    20: None,  # Service Closed (normal end of day)
}
DEFAULT_SEVERITY = ("minor", False)  # unknown future codes: alert conservatively, log a problem
SEVERITY_RANK = {"minor": 0, "severe": 1, "part_suspended": 2, "suspended": 3}
UNCERTAIN_SCALE = 0.5

# A full stop ends a clause unless it's the "St." in St. Paul's / St. John's Wood / St. James's Park.
_END = r"(?=\s+due to\b|\s+because\b|\s+while\b|\s+after\b|,|;|(?<!\bst)\.(?:\s|$)|\s-\s|$)"
BETWEEN = re.compile(r"\bbetween\s+(?P<span>.+?)" + _END, re.I)
# "Service operating between A and B" names where trains still RUN: the opposite of the disruption.
RUNNING_CONTEXT = re.compile(r"\b(?:operating|running|runs|operates)\s+(?:\w+\s+){0,2}$", re.I)
SINGLE_STATION = re.compile(
    r"(?:not stopping at|no service (?:at|to)|(?:is |are )?closed at|non-stop through)\s+(?P<s>(?:[^,.;]|(?<=\bst)\.)+?)(?=\s+(?:station|due|because|while)\b|,|;|(?<!\bst)\.(?:\s|$)|$)",
    re.I,
)
VIA = re.compile(r"\s+via\s+(?P<via>.+)$", re.I)


@dataclass(frozen=True)
class Incident:
    key: str
    line: str
    line_name: str
    severity_class: str
    status_code: int
    is_unplanned: bool
    station_ids: tuple[str, ...]
    line_wide: bool = False
    area_uncertain: bool = False
    severity_scale: float = 1.0
    reason: str = ""
    started_at: datetime | None = None  # TfL validityPeriods fromDate (isNow), when given


@dataclass
class ParseResult:
    incidents: list[Incident] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # sent to the builder, never silent (5A)


def parse_status(status: list[dict[str, Any]], network: Network) -> ParseResult:
    result = ParseResult()
    for line in status:
        line_id = line.get("id")
        if line_id not in network.lines:
            continue
        for ls in line.get("lineStatuses", []):
            _parse_line_status(line_id, ls, network, result)
    result.incidents = _dedupe(result.incidents)
    return result


def _parse_line_status(line_id: str, ls: dict[str, Any], network: Network, result: ParseResult) -> None:
    code = ls.get("statusSeverity")
    if code in SEVERITY_BY_CODE and SEVERITY_BY_CODE[code] is None:
        return
    if code not in SEVERITY_BY_CODE:
        result.problems.append(f"{line_id}: unknown statusSeverity {code!r} ({ls.get('statusSeverityDescription')}), treated as minor")
    severity, planned = SEVERITY_BY_CODE.get(code) or DEFAULT_SEVERITY

    disruption = ls.get("disruption") or {}
    category = disruption.get("category")
    is_unplanned = category == "RealTime" if category in ("RealTime", "PlannedWork") else not planned
    reason = " ".join((ls.get("reason") or disruption.get("description") or "").split())
    started_at = _started_at(ls)
    line = network.lines[line_id]

    def incident(section: tuple[str, ...] | None, *, uncertain: bool = False, tag: str = "") -> Incident:
        line_wide = section is None and not uncertain  # uncertain = whole line, still ranked (5A)
        ids = tuple(sorted(line.station_ids)) if uncertain else (() if line_wide else tuple(sorted(section)))
        key_src = "line-wide" if line_wide else (f"uncertain:{tag}" if uncertain else "|".join(ids))
        return Incident(
            key=f"{line_id}:{hashlib.sha1(key_src.encode()).hexdigest()[:10]}",
            line=line_id,
            line_name=line.name,
            severity_class=severity,
            status_code=code if isinstance(code, int) else -1,
            is_unplanned=is_unplanned,
            station_ids=ids,
            line_wide=line_wide,
            area_uncertain=uncertain,
            severity_scale=UNCERTAIN_SCALE if uncertain else 1.0,
            reason=reason,
            started_at=started_at,
        )

    affected = [network.naptan_to_station.get(s.get("naptanId") or s.get("id", "")) for s in disruption.get("affectedStops") or []]
    affected_on_line = {a for a in affected if a in line.station_ids}
    if affected_on_line:
        result.incidents.append(incident(tuple(affected_on_line)))
        return

    index = network.name_index(line_id)
    clauses = [m.group("span") for m in BETWEEN.finditer(reason) if not RUNNING_CONTEXT.search(reason[: m.start()])]
    for span in clauses:
        section = expand_clause(span, line_id, network, index)
        if section:
            result.incidents.append(incident(section))
        else:
            result.problems.append(f"{line_id}: could not place section 'between {span}' in: {reason}")
            result.incidents.append(incident(None, uncertain=True, tag=normalise(span)))

    if not clauses:
        singles = {sid for m in SINGLE_STATION.finditer(reason) for sid in index.get(normalise(m.group("s")), ())}
        if singles:
            result.incidents.append(incident(tuple(singles)))
        else:
            result.incidents.append(incident(None))


def expand_clause(span: str, line_id: str, network: Network, index: dict[str, tuple[str, ...]]) -> set[str] | None:
    """Stations covered by 'A and B' (A, B may hold '/' alternatives and 'via' hints).

    Station names can themselves contain 'and' ("Elephant and Castle"), so every ' and '
    split is tried and the first where both sides resolve is used.
    """
    words = span.split(" and ")
    for i in range(1, len(words)):
        left, right = " and ".join(words[:i]), " and ".join(words[i:])
        a_ends, b_ends = _endpoints(left, index), _endpoints(right, index)
        if a_ends is None or b_ends is None:
            continue
        covered: set[str] = set()
        for a_ids, a_via in a_ends:
            for b_ids, b_via in b_ends:
                path = _shortest_path(network, line_id, a_ids, b_ids, a_via | b_via)
                if path is None:
                    return None
                covered |= path
        return covered
    return None


def _endpoints(text: str, index: dict[str, tuple[str, ...]]) -> list[tuple[tuple[str, ...], frozenset[str]]] | None:
    """'Epping / Hainault via Newbury Park' -> [(Epping ids, {}), (Hainault ids, {Newbury Park ids})]."""
    ends = []
    for alt in text.split("/"):
        alt = alt.strip()
        via: frozenset[str] = frozenset()
        m = VIA.search(alt)
        if m:
            via_ids = index.get(normalise(m.group("via")))
            if via_ids is None:
                return None
            via = frozenset(via_ids)
            alt = alt[: m.start()]
        ids = index.get(normalise(alt))
        if ids is None:
            return None
        ends.append((ids, via))
    return ends or None


def _shortest_path(network: Network, line_id: str, a_ids: tuple[str, ...], b_ids: tuple[str, ...],
                   via: frozenset[str]) -> set[str] | None:
    best: tuple[str, ...] | None = None
    for route in network.lines[line_id].routes:
        stops = route.station_ids
        if via and not via & set(stops):
            continue
        for a in a_ids:
            for b in b_ids:
                if a in stops and b in stops:
                    i, j = sorted((stops.index(a), stops.index(b)))
                    segment = stops[i : j + 1]
                    if via and not via & set(segment):
                        continue
                    if best is None or len(segment) < len(best):
                        best = segment
    return set(best) if best is not None else None


def _started_at(ls: dict[str, Any]) -> datetime | None:
    for period in ls.get("validityPeriods") or []:
        if period.get("isNow") and period.get("fromDate"):
            try:
                return datetime.fromisoformat(period["fromDate"].replace("Z", "+00:00"))
            except ValueError:
                return None
    return None


def _dedupe(incidents: list[Incident]) -> list[Incident]:
    """Same key reported twice (e.g. two lineStatuses for one section): keep the most severe."""
    by_key: dict[str, Incident] = {}
    for inc in incidents:
        cur = by_key.get(inc.key)
        if cur is None or SEVERITY_RANK[inc.severity_class] > SEVERITY_RANK[cur.severity_class]:
            by_key[inc.key] = inc
    return list(by_key.values())
