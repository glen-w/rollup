"""Named run profiles (lookback / grouping habits) distinct from --effort."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from rollup.config import DEFAULT_LOOKBACK_DAYS
from rollup.user_config import STICKY_KEYS

DEFAULT_RUN_PROFILE = "weekly"


@dataclass(frozen=True)
class RunProfile:
    name: str
    description: str
    values: dict[str, Any]


def _builtin_profiles() -> dict[str, RunProfile]:
    return {
        "weekly": RunProfile(
            name="weekly",
            description="Weekly digest window (7 calendar days), grouping on",
            values={
                "lookback_days": DEFAULT_LOOKBACK_DAYS,
                "no_grouping": False,
            },
        ),
        "daily": RunProfile(
            name="daily",
            description="Daily digest window (1 calendar day), grouping on",
            values={
                "lookback_days": 1,
                "no_grouping": False,
            },
        ),
    }


class UnknownRunProfileError(ValueError):
    """Raised when a run profile name is not defined."""


def list_builtin_run_profiles() -> tuple[RunProfile, ...]:
    return tuple(_builtin_profiles().values())


def resolve_run_profile(
    name: str | None,
    *,
    toml_profiles: Mapping[str, Mapping[str, Any]] | None = None,
) -> RunProfile:
    """Resolve a run profile by name (builtins + TOML overlays)."""
    resolved_name = (name or DEFAULT_RUN_PROFILE).strip()
    builtins = _builtin_profiles()
    toml_profiles = toml_profiles or {}

    if resolved_name in builtins:
        base = builtins[resolved_name]
        overlay = dict(toml_profiles.get(resolved_name, {}))
        # TOML must not redefine profile name nesting oddly; ignore nested profile key.
        overlay.pop("profile", None)
        values = dict(base.values)
        values.update(overlay)
        description = base.description
        if overlay:
            description = f"{base.description} (with config overrides)"
        return RunProfile(
            name=resolved_name, description=description, values=values
        )

    if resolved_name in toml_profiles:
        body = dict(toml_profiles[resolved_name])
        body.pop("profile", None)
        # Unknown sticky keys already rejected at TOML load time.
        return RunProfile(
            name=resolved_name,
            description=f"Custom profile from config ({resolved_name})",
            values=body,
        )

    known = sorted(set(builtins) | set(toml_profiles))
    raise UnknownRunProfileError(
        f"Unknown run profile {resolved_name!r}. Known: {', '.join(known)}"
    )


def list_run_profiles(
    *,
    toml_profiles: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[RunProfile, ...]:
    builtins = list_builtin_run_profiles()
    toml_profiles = toml_profiles or {}
    custom = []
    for name in sorted(toml_profiles):
        if name in {p.name for p in builtins}:
            continue
        custom.append(resolve_run_profile(name, toml_profiles=toml_profiles))
    # Re-resolve builtins so TOML overlays show up.
    resolved_builtins = tuple(
        resolve_run_profile(p.name, toml_profiles=toml_profiles) for p in builtins
    )
    return resolved_builtins + tuple(custom)


# Keys a profile may contribute into the effective sticky merge.
PROFILE_VALUE_KEYS = STICKY_KEYS - frozenset({"profile"})
