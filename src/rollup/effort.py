"""Machine-power effort presets that bundle summary models and related defaults."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from rollup.config import (
    DEFAULT_EFFORT as _DEFAULT_EFFORT_STR,
    DEFAULT_MAX_CHARS_FOR_LLM,
    DEFAULT_OLLAMA_MODEL,
)
from rollup.summary_profiles import (
    SummaryProfile,
    SummaryProfileSet,
    get_builtin_summary_profile_set,
)

EffortName = Literal["light", "balanced", "high"]
DEFAULT_EFFORT: EffortName = _DEFAULT_EFFORT_STR  # type: ignore[assignment]
EFFORT_NAMES: tuple[EffortName, ...] = ("light", "balanced", "high")

# Final-review default matches builtin final_review_profiles (qwen2.5:7b).
DEFAULT_FINAL_REVIEW_MODEL = "qwen2.5:7b"


class UnknownEffortError(ValueError):
    """Raised when an effort preset name is not defined."""


@dataclass(frozen=True)
class EffortPreset:
    name: EffortName
    description: str
    profile_set: SummaryProfileSet
    ollama_model: str
    final_review_model: str
    max_chars_for_llm: int

    def expected_models(self) -> tuple[str, ...]:
        """Unique Ollama model tags this effort expects to have pulled."""
        models: list[str] = []
        seen: set[str] = set()
        for profile in self.profile_set.profiles.values():
            if profile.model not in seen:
                seen.add(profile.model)
                models.append(profile.model)
        for extra in (self.ollama_model, self.final_review_model):
            if extra not in seen:
                seen.add(extra)
                models.append(extra)
        return tuple(models)


def _replace_profile(
    base: SummaryProfile,
    *,
    model: str,
    num_ctx: int | None = None,
    timeout_seconds: int | None = None,
    num_predict: int | None = None,
    think: bool | str | None = None,
    description: str | None = None,
) -> SummaryProfile:
    kwargs: dict[str, object] = {"model": model}
    if num_ctx is not None:
        kwargs["num_ctx"] = num_ctx
    if timeout_seconds is not None:
        kwargs["timeout_seconds"] = timeout_seconds
    if num_predict is not None:
        kwargs["num_predict"] = num_predict
    if think is not None:
        kwargs["think"] = think
    if description is not None:
        kwargs["description"] = description
    return replace(base, **kwargs)


def _profile_set_from_profiles(
    name: EffortName,
    description: str,
    profiles: dict[str, SummaryProfile],
) -> SummaryProfileSet:
    base = get_builtin_summary_profile_set()
    return SummaryProfileSet(
        profiles=profiles,
        default_profile=base.default_profile,
        fallback_profile=base.fallback_profile,
        type_routes=dict(base.type_routes),
        name=name,
        description=description,
        schema_version=base.schema_version,
    )


def _balanced_preset() -> EffortPreset:
    base = get_builtin_summary_profile_set()
    profile_set = replace(
        base,
        name="balanced",
        description=(
            "Balanced effort: current Rollup defaults "
            "(3B rough → 7B standard → 20B deep → 27B max)."
        ),
    )
    return EffortPreset(
        name="balanced",
        description=profile_set.description or "Balanced effort",
        profile_set=profile_set,
        ollama_model=DEFAULT_OLLAMA_MODEL,
        final_review_model=DEFAULT_FINAL_REVIEW_MODEL,
        max_chars_for_llm=DEFAULT_MAX_CHARS_FOR_LLM,
    )


def _light_preset() -> EffortPreset:
    base = get_builtin_summary_profile_set().profiles
    profiles = {
        "rough": _replace_profile(
            base["rough"],
            model="llama3.2:3b",
            description="Light effort: fast rough summaries on a small model.",
        ),
        "standard": _replace_profile(
            base["standard"],
            model="llama3.2:3b",
            num_ctx=8192,
            timeout_seconds=60,
            num_predict=256,
            description="Light effort: balanced label on a small model.",
        ),
        "deep": _replace_profile(
            base["deep"],
            model="qwen2.5:7b",
            num_ctx=16384,
            timeout_seconds=120,
            num_predict=512,
            description="Light effort: deeper synthesis capped at 7B.",
        ),
        "max": _replace_profile(
            base["max"],
            model="qwen2.5:7b",
            num_ctx=16384,
            timeout_seconds=120,
            num_predict=512,
            description="Light effort: essay route capped at 7B.",
        ),
    }
    description = (
        "Light effort: smaller models for constrained machines "
        "(3B for rough/standard, 7B for deep/max)."
    )
    return EffortPreset(
        name="light",
        description=description,
        profile_set=_profile_set_from_profiles("light", description, profiles),
        ollama_model="llama3.2:3b",
        final_review_model="qwen2.5:7b",
        max_chars_for_llm=20_000,
    )


def _high_preset() -> EffortPreset:
    base = get_builtin_summary_profile_set().profiles
    profiles = {
        "rough": _replace_profile(
            base["rough"],
            model="qwen2.5:7b",
            num_ctx=16384,
            timeout_seconds=120,
            num_predict=512,
            description="High effort: rough summaries on 7B.",
        ),
        "standard": _replace_profile(
            base["standard"],
            model="gpt-oss:20b",
            num_ctx=32768,
            timeout_seconds=240,
            # gpt-oss always reasons; low + 2048 leaves room for visible output.
            num_predict=2048,
            think="low",
            description="High effort: default route on 20B.",
        ),
        "deep": _replace_profile(
            base["deep"],
            model="qwen3.6:27b",
            num_ctx=65536,
            timeout_seconds=600,
            num_predict=2048,
            description="High effort: deep synthesis on 27B.",
        ),
        "max": _replace_profile(
            base["max"],
            model="qwen3.6:27b",
            description="High effort: essay route on 27B with full budget.",
        ),
    }
    description = (
        "High effort: larger models for powerful machines "
        "(7B rough → 20B standard → 27B deep/max)."
    )
    return EffortPreset(
        name="high",
        description=description,
        profile_set=_profile_set_from_profiles("high", description, profiles),
        ollama_model="qwen2.5:7b",
        final_review_model="gpt-oss:20b",
        max_chars_for_llm=50_000,
    )


def get_effort_preset(name: str) -> EffortPreset:
    """Return a named effort preset or raise UnknownEffortError."""
    presets = {
        "light": _light_preset,
        "balanced": _balanced_preset,
        "high": _high_preset,
    }
    factory = presets.get(name)
    if factory is None:
        raise UnknownEffortError(
            f"Unknown effort {name!r}. Available: {', '.join(EFFORT_NAMES)}"
        )
    return factory()


def list_effort_presets() -> tuple[EffortPreset, ...]:
    """Return all built-in effort presets in display order."""
    return tuple(get_effort_preset(name) for name in EFFORT_NAMES)


def resolve_effort_name(name: str | None) -> EffortName:
    """Map None / omitted to the default effort."""
    if name is None:
        return DEFAULT_EFFORT
    if name not in EFFORT_NAMES:
        raise UnknownEffortError(
            f"Unknown effort {name!r}. Available: {', '.join(EFFORT_NAMES)}"
        )
    return name  # type: ignore[return-value]


def resolve_profile_set(
    *,
    effort: str | None = None,
    summary_profile_set_path: str | None = None,
):
    """Load a custom profile-set JSON, or the effort preset's built-in ladder."""
    from rollup.summary_profiles import load_summary_profile_set

    if summary_profile_set_path is not None:
        return load_summary_profile_set(summary_profile_set_path)
    return get_effort_preset(resolve_effort_name(effort)).profile_set
