"""Secrets and access list, from config.local.yaml (git-ignored) or environment variables.

Environment variables win, so a server or CI can run without the file:
    SURGE_TELEGRAM_BOT_TOKEN, SURGE_DISPATCHER_CHAT_ID, SURGE_DRIVER_CHAT_IDS (comma-separated),
    SURGE_BUILDER_CHAT_ID, SURGE_SUPABASE_URL, SURGE_SUPABASE_SERVICE_KEY, SURGE_DRIVER_HASH_SALT
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.local.yaml"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    dispatcher_chat_id: int | None = None  # None until you run /whoami and paste it in
    driver_chat_ids: frozenset[int] = field(default_factory=frozenset)
    builder_chat_id: int | None = None
    supabase_url: str | None = None
    supabase_service_key: str | None = None
    driver_hash_salt: str = ""

    def role(self, chat_id: int) -> str | None:
        """'dispatcher', 'driver' or None (not on the allowlist: ignored)."""
        if self.dispatcher_chat_id is not None and chat_id == self.dispatcher_chat_id:
            return "dispatcher"
        if chat_id in self.driver_chat_ids:
            return "driver"
        return None


def _int_or_none(v: Any) -> int | None:
    return None if v in (None, "") else int(v)


def load_config(path: Path = DEFAULT_PATH, env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ) if env is None else env
    raw: dict[str, Any] = {}
    if Path(path).exists():
        raw = yaml.safe_load(Path(path).read_text()) or {}

    def get(name: str) -> Any:
        return env.get(f"SURGE_{name.upper()}") or raw.get(name)

    token = get("telegram_bot_token")
    if not token:
        raise ConfigError(f"telegram_bot_token missing: set it in {path} or SURGE_TELEGRAM_BOT_TOKEN")
    drivers = get("driver_chat_ids") or []
    if isinstance(drivers, str):
        drivers = [d for d in drivers.split(",") if d.strip()]
    url, key = get("supabase_url"), get("supabase_service_key")
    if bool(url) != bool(key):
        raise ConfigError("set both supabase_url and supabase_service_key, or neither")
    salt = str(get("driver_hash_salt") or "")
    if drivers and len(salt) < 16:
        raise ConfigError("driver_hash_salt must be at least 16 random characters when drivers are configured")
    return Config(
        telegram_bot_token=str(token),
        dispatcher_chat_id=_int_or_none(get("dispatcher_chat_id")),
        driver_chat_ids=frozenset(int(d) for d in drivers),
        builder_chat_id=_int_or_none(get("builder_chat_id")),
        supabase_url=str(url) if url else None,
        supabase_service_key=str(key) if key else None,
        driver_hash_salt=salt,
    )
