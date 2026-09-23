"""Where the alerter keeps its memory: named JSON state documents, check-ins, current disruptions.

    Store (protocol)
      ├─ FileStore      data/state/*.json, checkins.jsonl   (local dev, tests, no Supabase yet)
      └─ SupabaseStore  PostgREST over HTTPS with the service key (worker only; eng review 2A)

State documents: "alerts" (lifecycle records), "tracker" (incident t0s), "settings" (dispatcher),
"telegram" (update offset), "problems" (what the builder was already told).
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Checkin:
    station_id: str
    status: str  # busy | normal | dead
    ts: datetime
    driver_hash: str

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["ts"] = self.ts.isoformat()
        return row

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Checkin:
        return cls(row["station_id"], row["status"], datetime.fromisoformat(row["ts"]), row["driver_hash"])


CHECKIN_STATUSES = ("busy", "normal", "dead")


class Store(Protocol):
    def get_state(self, name: str) -> dict[str, Any] | None: ...
    def put_state(self, name: str, value: dict[str, Any]) -> None: ...
    def add_checkin(self, c: Checkin) -> None: ...
    def checkins_since(self, t: datetime) -> list[Checkin]: ...
    def put_disruptions(self, rows: list[dict[str, Any]]) -> None: ...


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


class FileStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def get_state(self, name: str) -> dict[str, Any] | None:
        path = self.root / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def put_state(self, name: str, value: dict[str, Any]) -> None:
        _atomic_write(self.root / f"{name}.json", json.dumps(value, indent=1, sort_keys=True))

    def add_checkin(self, c: Checkin) -> None:
        with open(self.root / "checkins.jsonl", "a") as f:
            f.write(json.dumps(c.to_row()) + "\n")

    def checkins_since(self, t: datetime) -> list[Checkin]:
        path = self.root / "checkins.jsonl"
        if not path.exists():
            return []
        rows = [Checkin.from_row(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]
        return [c for c in rows if c.ts >= t]

    def put_disruptions(self, rows: list[dict[str, Any]]) -> None:
        _atomic_write(self.root / "disruptions.json", json.dumps(rows, indent=1))


HttpFn = Callable[[str, str, dict[str, str], bytes | None], bytes]


def _http(method: str, url: str, headers: dict[str, str], body: bytes | None) -> bytes:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


class SupabaseStore:
    """Tables from supabase/schema.sql: state, checkins, disruptions_current."""

    def __init__(self, url: str, service_key: str, http: HttpFn = _http) -> None:
        self.base = url.rstrip("/") + "/rest/v1"
        self.http = http
        self.headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}",
                        "Content-Type": "application/json"}

    def _req(self, method: str, path: str, body: Any = None, prefer: str | None = None) -> Any:
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode() if body is not None else None
        raw = self.http(method, self.base + path, headers, data)
        return json.loads(raw) if raw else None

    def get_state(self, name: str) -> dict[str, Any] | None:
        rows = self._req("GET", f"/state?name=eq.{urllib.parse.quote(name)}&select=value")
        return rows[0]["value"] if rows else None

    def put_state(self, name: str, value: dict[str, Any]) -> None:
        self._req("POST", "/state", {"name": name, "value": value, "updated_at": datetime.now().astimezone().isoformat()},
                  prefer="resolution=merge-duplicates,return=minimal")

    def add_checkin(self, c: Checkin) -> None:
        self._req("POST", "/checkins", c.to_row(), prefer="return=minimal")

    def checkins_since(self, t: datetime) -> list[Checkin]:
        rows = self._req("GET", f"/checkins?ts=gte.{urllib.parse.quote(t.isoformat())}&order=ts.asc")
        return [Checkin.from_row(r) for r in rows or []]

    def put_disruptions(self, rows: list[dict[str, Any]]) -> None:
        # Snapshot semantics: replace the whole current set.
        self._req("DELETE", "/disruptions_current?key=not.is.null", prefer="return=minimal")
        if rows:
            self._req("POST", "/disruptions_current", rows, prefer="return=minimal")
