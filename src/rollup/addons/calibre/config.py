"""Calibre watch-directory destination (no network)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CALIBRE_TOP_KEYS = frozenset({"drop_dir"})


@dataclass(frozen=True)
class CalibreConfig:
    """Copy finished briefing artifacts into a Calibre auto-add folder."""

    drop_dir: Path | None = None


def parse_calibre_config(raw: object | None, *, path: Path) -> CalibreConfig:
    if raw is None:
        return CalibreConfig()
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: [calibre] must be a table")

    unknown = set(raw) - CALIBRE_TOP_KEYS
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise ValueError(f"{path}: unknown key(s) in [calibre]: {keys}")

    drop_raw = raw.get("drop_dir")
    if drop_raw is None or drop_raw == "":
        return CalibreConfig()
    if not isinstance(drop_raw, str):
        raise ValueError(f"{path}: [calibre].drop_dir must be a string")
    return CalibreConfig(drop_dir=Path(drop_raw).expanduser())
