"""Interest-profile TOML configuration (not sticky)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

INTEREST_TOP_KEYS = frozenset({"profile"})
MAX_PROFILE_CHARS = 8_000


@dataclass(frozen=True)
class InterestConfig:
    """Prose interest profile. Empty means Quality / interaction / priority only."""

    profile: str = ""

    @property
    def present(self) -> bool:
        return bool(self.profile.strip())


def parse_interest_config(raw: object | None, *, path: Path) -> InterestConfig:
    if raw is None:
        return InterestConfig()
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: [interest] must be a table")
    unknown = set(raw) - INTEREST_TOP_KEYS
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise ValueError(f"{path}: unknown key(s) in [interest]: {keys}")
    profile = raw.get("profile", "")
    if profile is None:
        profile = ""
    if not isinstance(profile, str):
        raise ValueError(f"{path}: [interest].profile must be a string")
    if len(profile) > MAX_PROFILE_CHARS:
        raise ValueError(
            f"{path}: [interest].profile exceeds {MAX_PROFILE_CHARS} characters"
        )
    return InterestConfig(profile=profile.strip())


def load_interest_file(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Cannot read interest file {path}: {exc}") from exc
    if len(text) > MAX_PROFILE_CHARS:
        raise ValueError(
            f"{path}: interest file exceeds {MAX_PROFILE_CHARS} characters"
        )
    return text.strip()
