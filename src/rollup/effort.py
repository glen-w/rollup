"""Machine-power effort presets that bundle summary models and related defaults."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, Mapping

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

# Summary-profile slots whose models can be overridden per effort.
EFFORT_PROFILE_SLOTS: tuple[str, ...] = ("rough", "standard", "deep", "max")
EFFORT_COMPANION_KEYS: tuple[str, ...] = ("ollama_model", "final_review_model")
EFFORT_OVERRIDE_KEYS = frozenset(EFFORT_PROFILE_SLOTS) | frozenset(EFFORT_COMPANION_KEYS)


class UnknownEffortError(ValueError):
    """Raised when an effort preset name is not defined."""


@dataclass(frozen=True)
class EffortModelOverride:
    """Optional model substitutions for one built-in effort preset."""

    profiles: dict[str, str] = field(default_factory=dict)
    ollama_model: str | None = None
    final_review_model: str | None = None

    def is_empty(self) -> bool:
        return (
            not self.profiles
            and self.ollama_model is None
            and self.final_review_model is None
        )


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


def apply_effort_override(
    preset: EffortPreset,
    override: EffortModelOverride | None,
) -> EffortPreset:
    """Substitute models on a built-in preset. Empty/None override is a no-op."""
    if override is None or override.is_empty():
        return preset
    profiles = dict(preset.profile_set.profiles)
    for slot, model in override.profiles.items():
        if slot not in profiles:
            raise UnknownEffortError(
                f"Unknown profile slot {slot!r} in effort {preset.name} override. "
                f"Available: {', '.join(EFFORT_PROFILE_SLOTS)}"
            )
        profiles[slot] = replace(profiles[slot], model=model)
    profile_set = replace(preset.profile_set, profiles=profiles)
    return replace(
        preset,
        profile_set=profile_set,
        ollama_model=override.ollama_model or preset.ollama_model,
        final_review_model=override.final_review_model or preset.final_review_model,
    )


def get_effort_preset(
    name: str,
    override: EffortModelOverride | None = None,
) -> EffortPreset:
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
    return apply_effort_override(factory(), override)


def list_effort_presets(
    overrides: Mapping[str, EffortModelOverride] | None = None,
) -> tuple[EffortPreset, ...]:
    """Return all built-in effort presets in display order."""
    ov = overrides or {}
    return tuple(
        get_effort_preset(name, override=ov.get(name)) for name in EFFORT_NAMES
    )


def resolve_effort_name(name: str | None) -> EffortName:
    """Map None / omitted to the default effort."""
    if name is None:
        return DEFAULT_EFFORT
    if name not in EFFORT_NAMES:
        raise UnknownEffortError(
            f"Unknown effort {name!r}. Available: {', '.join(EFFORT_NAMES)}"
        )
    return name  # type: ignore[return-value]


def apply_single_model(
    profile_set: SummaryProfileSet,
    model: str,
    *,
    provider: str = "ollama",
) -> SummaryProfileSet:
    """Point every summary profile at one model. Empty model is a no-op."""
    model = model.strip()
    if not model:
        return profile_set
    profiles: dict[str, SummaryProfile] = {}
    for name, profile in profile_set.profiles.items():
        if provider == "litellm":
            profiles[name] = replace(
                profile,
                model=model,
                provider="litellm",
                think=False,
                num_ctx=None,
                options={},
            )
        else:
            profiles[name] = replace(profile, model=model, provider="ollama")
    return replace(profile_set, profiles=profiles)


def apply_single_model_to_preset(
    preset: EffortPreset,
    model: str,
    *,
    provider: str = "ollama",
) -> EffortPreset:
    """Swap every ladder + companion model; effort budgets stay as they are."""
    model = model.strip()
    if not model:
        return preset
    return replace(
        preset,
        profile_set=apply_single_model(
            preset.profile_set, model, provider=provider
        ),
        ollama_model=model,
        final_review_model=model,
    )


def resolve_profile_set(
    *,
    effort: str | None = None,
    summary_profile_set_path: str | None = None,
    effort_overrides: Mapping[str, EffortModelOverride] | None = None,
    single_model: str | None = None,
    llm_provider: str | None = None,
):
    """Load a custom profile-set JSON, or the effort preset's built-in ladder."""
    from rollup.summary_profiles import load_summary_profile_set

    if summary_profile_set_path is not None:
        profile_set = load_summary_profile_set(summary_profile_set_path)
    else:
        name = resolve_effort_name(effort)
        override = (effort_overrides or {}).get(name)
        profile_set = get_effort_preset(name, override=override).profile_set
    if single_model:
        profile_set = apply_single_model(
            profile_set,
            single_model,
            provider=llm_provider or "ollama",
        )
    return profile_set


def effort_editor_rows(
    overrides: Mapping[str, EffortModelOverride] | None = None,
) -> list[dict[str, object]]:
    """UI rows for editing per-effort models. Placeholders are built-in defaults."""
    ov = overrides or {}
    slot_labels = {
        "rough": "Rough",
        "standard": "Standard",
        "deep": "Deep",
        "max": "Max",
        "ollama_model": "Group / fallback",
        "final_review_model": "Final review",
    }
    rows: list[dict[str, object]] = []
    for preset in list_effort_presets():
        override = ov.get(preset.name)
        slots: list[dict[str, str]] = []
        for name in EFFORT_PROFILE_SLOTS:
            default = preset.profile_set.profiles[name].model
            value = (override.profiles.get(name) if override else None) or ""
            slots.append(
                {
                    "name": name,
                    "label": slot_labels[name],
                    "default": default,
                    "value": value,
                }
            )
        slots.append(
            {
                "name": "ollama_model",
                "label": slot_labels["ollama_model"],
                "default": preset.ollama_model,
                "value": (override.ollama_model if override else None) or "",
            }
        )
        slots.append(
            {
                "name": "final_review_model",
                "label": slot_labels["final_review_model"],
                "default": preset.final_review_model,
                "value": (override.final_review_model if override else None) or "",
            }
        )
        rows.append(
            {
                "name": preset.name,
                "description": preset.description,
                "slots": slots,
            }
        )
    return rows
