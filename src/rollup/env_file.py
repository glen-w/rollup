"""Load optional Rollup secrets from a local env file (never TOML)."""

from __future__ import annotations

import os
import re
from pathlib import Path

_ENV_LINE = re.compile(
    r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$"
)

ROLLUP_ENV_FILE_ENV = "ROLLUP_ENV_FILE"


def default_env_file_path() -> Path:
    """Default secrets file beside config.toml."""
    return Path.home() / ".config" / "rollup" / "env"


def resolve_env_file_paths() -> tuple[Path, ...]:
    """Paths checked in order; first existing file wins."""
    override = os.environ.get(ROLLUP_ENV_FILE_ENV, "").strip()
    if override:
        return (Path(override).expanduser(),)
    return (default_env_file_path(),)


def _strip_env_value(raw: str) -> str:
    text = raw.strip()
    if not text or text[0] not in {'"', "'"}:
        return text
    quote = text[0]
    if len(text) >= 2 and text.endswith(quote):
        return text[1:-1]
    return text


def parse_env_file_text(text: str) -> dict[str, str]:
    """Parse KEY=value lines (optional ``export`` prefix)."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_LINE.match(stripped)
        if not match:
            continue
        key, raw_value = match.group(1), match.group(2)
        values[key] = _strip_env_value(raw_value)
    return values


def load_rollup_env(*, paths: tuple[Path, ...] | None = None) -> Path | None:
    """Load secrets into ``os.environ`` without overriding existing vars.

    Returns the path of the file that was loaded, or ``None`` when no file exists.
    """
    for path in paths if paths is not None else resolve_env_file_paths():
        expanded = path.expanduser()
        if not expanded.is_file():
            continue
        try:
            text = expanded.read_text(encoding="utf-8")
        except OSError:
            continue
        for key, value in parse_env_file_text(text).items():
            if key not in os.environ:
                os.environ[key] = value
        return expanded
    return None
