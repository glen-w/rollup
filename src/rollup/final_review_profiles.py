"""Builtin final review profiles for digest-level QA."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

FINAL_REVIEW_PROVIDERS = frozenset({"ollama"})
FINAL_REVIEW_PROMPT_STYLES = frozenset({"strict", "concise", "editorial"})
FINAL_REVIEW_MAX_OUTPUT_CHARS = 16_000


class FinalReviewConfigError(ValueError):
    """Raised when final review configuration is invalid."""


class UnknownFinalReviewProfileError(FinalReviewConfigError):
    """Raised when a named final review profile is not defined."""


@dataclass(frozen=True)
class FinalReviewProfile:
    name: str
    provider: str
    model: str
    temperature: float
    num_ctx: int | None = None
    timeout_seconds: int = 120
    prompt_style: str = "strict"
    options: dict[str, Any] = field(default_factory=dict)


def _make_profile(
    name: str,
    model: str,
    prompt_style: str,
    *,
    temperature: float,
    num_ctx: int | None = 8192,
    timeout_seconds: int = 120,
) -> FinalReviewProfile:
    return FinalReviewProfile(
        name=name,
        provider="ollama",
        model=model,
        temperature=temperature,
        num_ctx=num_ctx,
        timeout_seconds=timeout_seconds,
        prompt_style=prompt_style,
        options={"format": "json"},
    )


_BUILTIN_PROFILES: dict[str, FinalReviewProfile] = {
    "strict": _make_profile(
        "strict",
        "qwen2.5:7b",
        "strict",
        temperature=0.1,
    ),
    "concise": _make_profile(
        "concise",
        "qwen2.5:7b",
        "concise",
        temperature=0.1,
    ),
    "editorial": _make_profile(
        "editorial",
        "qwen2.5:7b",
        "editorial",
        temperature=0.15,
    ),
}


def get_builtin_final_review_profiles() -> dict[str, FinalReviewProfile]:
    return dict(_BUILTIN_PROFILES)


def resolve_final_review_profile(
    profile_name: str,
    *,
    model_override: str | None = None,
) -> FinalReviewProfile:
    profile = _BUILTIN_PROFILES.get(profile_name)
    if profile is None:
        raise UnknownFinalReviewProfileError(
            f"Unknown final review profile {profile_name!r}. "
            f"Available: {', '.join(sorted(_BUILTIN_PROFILES))}"
        )
    if model_override:
        return FinalReviewProfile(
            name=profile.name,
            provider=profile.provider,
            model=model_override,
            temperature=profile.temperature,
            num_ctx=profile.num_ctx,
            timeout_seconds=profile.timeout_seconds,
            prompt_style=profile.prompt_style,
            options=dict(profile.options),
        )
    return profile


def validate_final_review_config(
    *,
    mode: str,
    provider: str,
    profile_name: str,
) -> None:
    if mode == "apply":
        raise FinalReviewConfigError("apply mode is not implemented yet")
    if mode != "report":
        raise FinalReviewConfigError(
            f"Invalid final review mode {mode!r}; expected 'report'."
        )
    if provider not in FINAL_REVIEW_PROVIDERS:
        raise FinalReviewConfigError(
            f"Unsupported final review provider {provider!r}; "
            f"supported: {', '.join(sorted(FINAL_REVIEW_PROVIDERS))}"
        )
    if profile_name not in _BUILTIN_PROFILES:
        raise UnknownFinalReviewProfileError(
            f"Unknown final review profile {profile_name!r}. "
            f"Available: {', '.join(sorted(_BUILTIN_PROFILES))}"
        )
