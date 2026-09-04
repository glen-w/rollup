"""Google Scholar TOML configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ScholarMode = Literal["default", "detailed"]
SCHOLAR_MODES = frozenset({"default", "detailed"})
SCHOLAR_TOP_KEYS = frozenset(
    {"mode", "max_papers_per_email", "max_fetches_per_run"}
)

DEFAULT_SCHOLAR_MODE: ScholarMode = "default"
DEFAULT_MAX_PAPERS_PER_EMAIL = 8
DEFAULT_MAX_FETCHES_PER_RUN = 40
MAX_PAPERS_PER_EMAIL = 50
MAX_FETCHES_PER_RUN = 200
SCHOLAR_FETCH_BACKOFF_SECONDS = 1.0


@dataclass(frozen=True)
class ScholarConfig:
    mode: ScholarMode = DEFAULT_SCHOLAR_MODE
    max_papers_per_email: int = DEFAULT_MAX_PAPERS_PER_EMAIL
    max_fetches_per_run: int = DEFAULT_MAX_FETCHES_PER_RUN

    @property
    def detailed(self) -> bool:
        return self.mode == "detailed"


def _parse_positive_int(
    raw: object,
    *,
    default: int,
    ceiling: int,
    path: Path,
    context: str,
) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{path}: {context} must be an integer")
    if raw < 1:
        raise ValueError(f"{path}: {context} must be >= 1")
    return min(raw, ceiling)


def parse_scholar_config(raw: object | None, *, path: Path) -> ScholarConfig:
    if raw is None:
        return ScholarConfig()
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: [scholar] must be a table")

    unknown = set(raw) - SCHOLAR_TOP_KEYS
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise ValueError(f"{path}: unknown key(s) in [scholar]: {keys}")

    mode = raw.get("mode", DEFAULT_SCHOLAR_MODE)
    if mode not in SCHOLAR_MODES:
        raise ValueError(f"{path}: [scholar].mode must be default or detailed")

    max_papers = _parse_positive_int(
        raw.get("max_papers_per_email", DEFAULT_MAX_PAPERS_PER_EMAIL),
        default=DEFAULT_MAX_PAPERS_PER_EMAIL,
        ceiling=MAX_PAPERS_PER_EMAIL,
        path=path,
        context="[scholar].max_papers_per_email",
    )
    max_fetches = _parse_positive_int(
        raw.get("max_fetches_per_run", DEFAULT_MAX_FETCHES_PER_RUN),
        default=DEFAULT_MAX_FETCHES_PER_RUN,
        ceiling=MAX_FETCHES_PER_RUN,
        path=path,
        context="[scholar].max_fetches_per_run",
    )
    return ScholarConfig(
        mode=mode,  # type: ignore[arg-type]
        max_papers_per_email=max_papers,
        max_fetches_per_run=max_fetches,
    )
