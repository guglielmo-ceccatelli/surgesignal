"""Load and validate params.yaml into a typed, immutable Params object.

Fail fast: a missing, misspelled, non-numeric or undocumented parameter stops the worker
at start-up instead of silently using a wrong number.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "params.yaml"


@dataclass(frozen=True)
class Params:
    severity_suspended: float
    severity_part_suspended: float
    severity_severe: float
    severity_minor: float
    unplanned_multiplier: float
    planned_multiplier: float
    lambda_km: float
    ramp_min: float
    decay_half_life_min: float
    default_duration_minor_min: float
    default_duration_severe_min: float
    default_duration_part_suspended_min: float
    default_duration_suspended_min: float
    dry_threshold_mm_h: float
    heavy_rain_threshold_mm_h: float
    light_rain_multiplier: float
    heavy_rain_multiplier: float
    cold_threshold_c: float
    cold_bonus: float
    event_radius_km: float
    event_before_min: float
    event_after_min: float
    event_medium_capacity: float
    event_medium_multiplier: float
    event_large_capacity: float
    event_large_multiplier: float
    range_low_factor: float
    range_high_factor: float
    alert_threshold_uplift: float
    sizing_k: float
    incident_merge_window_min: float
    resolved_retention_min: float
    lookahead_min_uplift: float
    lookahead_days: float
    top_n: int
    default_service_radius_km: float

    def severity(self, severity_class: str) -> float:
        return getattr(self, f"severity_{severity_class}")

    def default_duration_min(self, severity_class: str) -> float:
        return getattr(self, f"default_duration_{severity_class}_min")


class ParamsError(ValueError):
    pass


REQUIRED_META = ("value", "unit", "source")


def load_raw(path: Path = DEFAULT_PATH) -> dict[str, dict[str, Any]]:
    """The documented entries (value, unit, source, note), for the params panel and write-up."""
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict):
        raise ParamsError(f"{path}: expected a mapping of parameter entries")
    return data


def load_params(path: Path = DEFAULT_PATH) -> Params:
    raw = load_raw(path)
    expected = {f.name: f for f in fields(Params)}

    unknown = sorted(set(raw) - set(expected))
    missing = sorted(set(expected) - set(raw))
    if unknown or missing:
        raise ParamsError(f"{path}: unknown={unknown} missing={missing}")

    values: dict[str, Any] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict) or any(k not in entry for k in REQUIRED_META):
            raise ParamsError(f"{path}: '{name}' needs {', '.join(REQUIRED_META)}")
        value = entry["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ParamsError(f"{path}: '{name}' value must be a number, got {value!r}")
        if value < 0:
            raise ParamsError(f"{path}: '{name}' must not be negative, got {value}")
        values[name] = int(value) if expected[name].type == "int" else float(value)
    return Params(**values)


def params_markdown_table(path: Path = DEFAULT_PATH) -> str:
    """Render params.yaml as the write-up's parameter table (same source as the engine)."""
    rows = ["| Parameter | Value | Unit | Source |", "|---|---|---|---|"]
    for name, entry in load_raw(path).items():
        rows.append(f"| `{name}` | {entry['value']} | {entry['unit']} | {entry['source']} |")
    return "\n".join(rows)
