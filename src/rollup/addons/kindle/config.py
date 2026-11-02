"""Send-to-Kindle destination. Address in TOML; SMTP secrets in the environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

KINDLE_KEYS = frozenset({"to", "from"})
SMTP_HOST_ENV = "ROLLUP_KINDLE_SMTP_HOST"
SMTP_PORT_ENV = "ROLLUP_KINDLE_SMTP_PORT"
SMTP_USER_ENV = "ROLLUP_KINDLE_SMTP_USER"
SMTP_PASSWORD_ENV = "ROLLUP_KINDLE_SMTP_PASSWORD"
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class KindleConfig:
    to: str | None = None
    sender: str | None = None


def parse_kindle_config(raw: object, *, path: Path) -> KindleConfig:
    if raw is None:
        return KindleConfig()
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path}: [kindle] must be a table")
    unknown = set(raw) - KINDLE_KEYS
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise ValueError(f"{path}: unknown key(s) in [kindle]: {keys}")
    to = raw.get("to")
    sender = raw.get("from")
    if to is not None and not isinstance(to, str):
        raise ValueError(f"{path}: [kindle].to must be a string")
    if sender is not None and not isinstance(sender, str):
        raise ValueError(f"{path}: [kindle].from must be a string")
    to_s = to.strip() if isinstance(to, str) else ""
    from_s = sender.strip() if isinstance(sender, str) else ""
    if to_s and "@" not in to_s:
        raise ValueError(f"{path}: [kindle].to must be an email address")
    if from_s and "@" not in from_s:
        raise ValueError(f"{path}: [kindle].from must be an email address")
    return KindleConfig(to=to_s or None, sender=from_s or None)


def smtp_env() -> dict[str, str | None]:
    host = os.environ.get(SMTP_HOST_ENV, "").strip() or None
    user = os.environ.get(SMTP_USER_ENV, "").strip() or None
    password = os.environ.get(SMTP_PASSWORD_ENV, "").strip() or None
    port_raw = os.environ.get(SMTP_PORT_ENV, "").strip()
    port = port_raw or "587"
    return {"host": host, "port": port, "user": user, "password": password}


def smtp_credentials_present() -> bool:
    env = smtp_env()
    return bool(env["host"] and env["password"])
