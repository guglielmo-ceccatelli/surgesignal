"""Raw API response store: gzipped JSON on local disk, written only when content changes.

Raw logs are the one dataset that can never be rebuilt, so this module keeps the exact
bytes TfL sent and decides "changed?" on a content hash that ignores volatile fields.

    payload bytes ──► json.loads ──► strip created/modified ──► sha256 (sorted keys)
                                                                    │
                                          same as last saved hash? ─┤
                                              yes ──► skip (return None)
                                              no  ──► <root>/<feed>/<YYYY-MM-DD>/<HHMMSS>Z.json.gz
                                                      + remember hash in <root>/<feed>/.last_hash

The last hash is persisted, so a restarted logger doesn't re-save an unchanged response.
All timestamps are UTC.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Fields TfL may bump without the underlying status changing.
VOLATILE_KEYS = frozenset({"created", "modified"})


def strip_volatile(obj: Any) -> Any:
    """Return a copy of a parsed JSON value with VOLATILE_KEYS removed at every depth."""
    if isinstance(obj, dict):
        return {k: strip_volatile(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [strip_volatile(v) for v in obj]
    return obj


def content_hash(obj: Any) -> str:
    """Stable sha256 of a parsed JSON value, ignoring volatile fields and key order."""
    canonical = json.dumps(strip_volatile(obj), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RawLog:
    """Change-only gzip store for one feed (e.g. "status", "disruption", "bikepoint")."""

    def __init__(self, root: Path, feed: str) -> None:
        self.dir = Path(root) / feed
        self.dir.mkdir(parents=True, exist_ok=True)
        self._hash_file = self.dir / ".last_hash"
        self._last_hash = self._hash_file.read_text().strip() if self._hash_file.exists() else None

    def save_if_changed(self, payload: bytes, fetched_at: datetime) -> Path | None:
        """Write payload if its content differs from the last saved one.

        Raises ValueError if payload isn't JSON (so a TfL HTML error page is never stored
        as data). Returns the written path, or None when the content is unchanged.
        """
        if fetched_at.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware (UTC)")
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"payload is not JSON: {exc}") from exc

        digest = content_hash(parsed)
        if digest == self._last_hash:
            return None

        ts = fetched_at.astimezone(timezone.utc)
        day_dir = self.dir / ts.strftime("%Y-%m-%d")
        day_dir.mkdir(exist_ok=True)
        path = day_dir / f"{ts.strftime('%H%M%S')}Z.json.gz"

        # Write-then-rename so a crash mid-write never leaves a truncated file behind.
        tmp = path.with_suffix(".gz.tmp")
        with gzip.open(tmp, "wb") as f:
            f.write(payload)
        os.replace(tmp, path)

        self._hash_file.write_text(digest)
        self._last_hash = digest
        return path
