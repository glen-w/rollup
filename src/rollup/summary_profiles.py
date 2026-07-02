"""Summary profile configuration, validation, and serialization."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, get_args

from rollup.models import NewsletterType

SUMMARY_PROFILE_SCHEMA_VERSION = 1
SUMMARY_PROVIDERS = frozenset({"ollama"})
PROMPT_STYLES = frozenset({"rough", "standard", "deep"})
ROUTING_RESERVED_KEYS = frozenset({"default"})
DEFAULT_NUM_PREDICT = 2048
DEFAULT_THINK = False
OLLAMA_OPTIONS_RESERVED_KEYS = frozenset({"num_predict", "think"})
CACHE_THINK_IDENTITY_KEY = "__rollup_think__"


class SummaryConfigError(ValueError):
    """Raised when summary profile configuration is invalid."""


class UnknownSummaryProfileError(SummaryConfigError):
    """Raised when a named summary profile is not defined."""


class DisabledSummaryProfileError(SummaryConfigError):
    """Raised when a disabled summary profile is referenced."""


class UnknownNewsletterTypeError(SummaryConfigError):
    """Raised when a summary route references an unknown classifier label."""


@dataclass(frozen=True)
class SummaryProfile:
    name: str
    provider: str
    model: str
    temperature: float
    num_ctx: int | None = None
    timeout_seconds: int | None = None
    prompt_style: str = "standard"
    num_predict: int = DEFAULT_NUM_PREDICT
    think: bool = DEFAULT_THINK
    options: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    description: str | None = None
    created_by: str | None = None
    version: str | int | None = None


@dataclass(frozen=True)
class SummaryProfileSet:
    profiles: dict[str, SummaryProfile]
    default_profile: str
    type_routes: dict[str, str]
    fallback_profile: str | None = None
    name: str | None = None
    description: str | None = None
    schema_version: int = SUMMARY_PROFILE_SCHEMA_VERSION


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    path: str
    severity: str = "error"


def _disabled_profile_issue(path: str, profile_name: str) -> ValidationIssue:
    return ValidationIssue(
        code="disabled_profile_reference",
        message=f"Profile {profile_name!r} is disabled.",
        path=path,
    )


@dataclass(frozen=True)
class SummaryProfileInfo:
    name: str
    provider: str
    model: str
    prompt_style: str
    temperature: float
    num_ctx: int | None
    timeout_seconds: int | None
    num_predict: int
    think: bool
    enabled: bool
    description: str | None
    created_by: str | None
    version: str | int | None


@dataclass(frozen=True)
class TypeRouteInfo:
    newsletter_type: str
    profile_name: str
    is_reserved_key: bool


def get_canonical_newsletter_types() -> tuple[str, ...]:
    """Return the canonical classifier labels used by the pipeline."""
    return tuple(get_args(NewsletterType))


def _make_profile(
    name: str,
    model: str,
    prompt_style: str,
    *,
    temperature: float,
    num_ctx: int | None,
    timeout_seconds: int | None,
    description: str,
    num_predict: int = DEFAULT_NUM_PREDICT,
    think: bool = DEFAULT_THINK,
    options: dict[str, Any] | None = None,
) -> SummaryProfile:
    return SummaryProfile(
        name=name,
        provider="ollama",
        model=model,
        temperature=temperature,
        num_ctx=num_ctx,
        timeout_seconds=timeout_seconds,
        prompt_style=prompt_style,
        num_predict=num_predict,
        think=think,
        options=dict(options or {}),
        enabled=True,
        description=description,
        created_by="builtin",
        version=1,
    )


def resolve_profile_ollama_options(profile: SummaryProfile) -> dict[str, Any]:
    """Build the Ollama `options` object for a profile."""
    opts = {
        key: value
        for key, value in profile.options.items()
        if key not in OLLAMA_OPTIONS_RESERVED_KEYS
    }
    opts["num_predict"] = profile.num_predict
    return opts


def summary_job_options_for_cache(
    options: dict[str, Any], *, think: bool
) -> dict[str, Any]:
    """Extend generation options with cache identity for `think`."""
    cached = dict(options)
    cached[CACHE_THINK_IDENTITY_KEY] = think
    return cached


def get_builtin_summary_profile_set() -> SummaryProfileSet:
    """Return built-in summary profiles and default type routes."""
    profiles = {
        "rough": _make_profile(
            "rough",
            "llama3.2:3b",
            "rough",
            temperature=0.2,
            num_ctx=8192,
            timeout_seconds=60,
            description="Quick rough summary for low-value or link-heavy items.",
            num_predict=256,
        ),
        "standard": _make_profile(
            "standard",
            "qwen2.5:7b",
            "standard",
            temperature=0.2,
            num_ctx=16384,
            timeout_seconds=120,
            description="Default balanced summary profile.",
            num_predict=512,
        ),
        "deep": _make_profile(
            "deep",
            "gpt-oss:20b",
            "deep",
            temperature=0.2,
            num_ctx=32768,
            timeout_seconds=240,
            description="Higher-effort synthesis for analytical or policy-heavy items.",
            num_predict=1024,
        ),
        "max": _make_profile(
            "max",
            "qwen3.6:27b",
            "deep",
            temperature=0.2,
            num_ctx=65536,
            timeout_seconds=600,
            description="Highest-effort profile for long essays and strategic reads.",
            num_predict=DEFAULT_NUM_PREDICT,
            think=DEFAULT_THINK,
        ),
    }
    return SummaryProfileSet(
        profiles=profiles,
        default_profile="standard",
        fallback_profile="standard",
        type_routes={
            "short_update": "rough",
            "link_roundup": "rough",
            "multi_section_digest": "standard",
            "essay": "max",
            "unclassified": "standard",
        },
        name="builtin",
        description="Built-in summary profiles for Rollup.",
        schema_version=SUMMARY_PROFILE_SCHEMA_VERSION,
    )


def _profile_from_dict(name: str, raw: dict[str, Any]) -> SummaryProfile:
    options = dict(raw.get("options", {}))
    num_predict_raw = raw.get("num_predict")
    if num_predict_raw is None:
        num_predict_raw = options.pop("num_predict", DEFAULT_NUM_PREDICT)
    else:
        options.pop("num_predict", None)
    think_raw = raw.get("think")
    if think_raw is None:
        think_raw = options.pop("think", DEFAULT_THINK)
    else:
        options.pop("think", None)
    for reserved_key in OLLAMA_OPTIONS_RESERVED_KEYS:
        options.pop(reserved_key, None)
    return SummaryProfile(
        name=name,
        provider=str(raw.get("provider", "ollama")),
        model=str(raw.get("model", "")),
        temperature=float(raw.get("temperature", 0.2)),
        num_ctx=raw.get("num_ctx"),
        timeout_seconds=raw.get("timeout_seconds"),
        prompt_style=str(raw.get("prompt_style", "standard")),
        num_predict=int(num_predict_raw),
        think=bool(think_raw),
        options=options,
        enabled=bool(raw.get("enabled", True)),
        description=raw.get("description"),
        created_by=raw.get("created_by"),
        version=raw.get("version"),
    )


def summary_profile_set_to_dict(profile_set: SummaryProfileSet) -> dict[str, Any]:
    """Serialize a profile set to plain JSON-safe data."""
    return {
        "schema_version": profile_set.schema_version,
        "name": profile_set.name,
        "description": profile_set.description,
        "default_profile": profile_set.default_profile,
        "fallback_profile": profile_set.fallback_profile,
        "type_routes": dict(profile_set.type_routes),
        "profiles": {
            name: asdict(profile) for name, profile in profile_set.profiles.items()
        },
    }


def summary_profile_set_from_dict(data: dict[str, Any]) -> SummaryProfileSet:
    """Deserialize a profile set from plain data."""
    profiles_raw = dict(data.get("profiles", {}))
    profiles = {
        name: _profile_from_dict(name, raw) for name, raw in profiles_raw.items()
    }
    return SummaryProfileSet(
        profiles=profiles,
        default_profile=str(data.get("default_profile", "")),
        fallback_profile=data.get("fallback_profile"),
        type_routes=dict(data.get("type_routes", {})),
        name=data.get("name"),
        description=data.get("description"),
        schema_version=int(data.get("schema_version", SUMMARY_PROFILE_SCHEMA_VERSION)),
    )


def _apply_override(
    base: SummaryProfileSet, overrides: dict[str, Any]
) -> SummaryProfileSet:
    data = summary_profile_set_to_dict(base)
    for key, value in overrides.items():
        if key == "profiles":
            merged = dict(data.get("profiles", {}))
            for profile_name, profile_override in dict(value).items():
                merged_profile = dict(merged.get(profile_name, {}))
                merged_profile.update(profile_override)
                merged[profile_name] = merged_profile
            data["profiles"] = merged
        elif key == "type_routes":
            merged_routes = dict(data.get("type_routes", {}))
            merged_routes.update(dict(value))
            data["type_routes"] = merged_routes
        else:
            data[key] = value
    return summary_profile_set_from_dict(data)


def load_summary_profile_set(
    config_path: str | Path | None = None, overrides: dict[str, Any] | None = None
) -> SummaryProfileSet:
    """Load built-in profiles or an exact user-provided profile-set file."""
    if config_path is not None:
        input_path = Path(config_path)
        if not input_path.is_file():
            raise SummaryConfigError(f"Summary profile set not found: {config_path}")
        profile_set = import_summary_profile_set(input_path)
        if not profile_set.profiles:
            raise SummaryConfigError(
                f"Summary profile set {config_path} defines no profiles."
            )
    else:
        profile_set = get_builtin_summary_profile_set()
    if overrides:
        profile_set = _apply_override(profile_set, overrides)
    return profile_set


def validate_summary_profile_set(
    profile_set: SummaryProfileSet,
    known_newsletter_types: tuple[str, ...] | None = None,
) -> list[ValidationIssue]:
    """Return structured validation issues without exiting the process."""
    known_types = set(known_newsletter_types or get_canonical_newsletter_types())
    issues: list[ValidationIssue] = []
    if profile_set.schema_version < 1:
        issues.append(
            ValidationIssue(
                code="invalid_schema_version",
                message="schema_version must be >= 1.",
                path="schema_version",
            )
        )
    if not profile_set.profiles:
        issues.append(
            ValidationIssue(
                code="missing_profiles",
                message="At least one summary profile must be defined.",
                path="profiles",
            )
        )
    for profile_name in (profile_set.default_profile, profile_set.fallback_profile):
        if profile_name and profile_name not in profile_set.profiles:
            issues.append(
                ValidationIssue(
                    code="unknown_profile_reference",
                    message=f"Profile {profile_name!r} is not defined.",
                    path=(
                        "default_profile"
                        if profile_name == profile_set.default_profile
                        else "fallback_profile"
                    ),
                )
            )
        elif profile_name:
            profile = profile_set.profiles.get(profile_name)
            if profile is not None and not profile.enabled:
                issues.append(
                    _disabled_profile_issue(
                        (
                            "default_profile"
                            if profile_name == profile_set.default_profile
                            else "fallback_profile"
                        ),
                        profile_name,
                    )
                )
    for name, profile in profile_set.profiles.items():
        if not profile.model:
            issues.append(
                ValidationIssue(
                    code="missing_model",
                    message=f"Profile {name!r} must define a model.",
                    path=f"profiles.{name}.model",
                )
            )
        if profile.provider not in SUMMARY_PROVIDERS:
            issues.append(
                ValidationIssue(
                    code="unsupported_provider",
                    message=f"Provider {profile.provider!r} is not supported.",
                    path=f"profiles.{name}.provider",
                )
            )
        if profile.prompt_style not in PROMPT_STYLES:
            issues.append(
                ValidationIssue(
                    code="invalid_prompt_style",
                    message=f"Prompt style {profile.prompt_style!r} is invalid.",
                    path=f"profiles.{name}.prompt_style",
                )
            )
        if profile.num_predict < 1:
            issues.append(
                ValidationIssue(
                    code="invalid_num_predict",
                    message=f"Profile {name!r} must define num_predict >= 1.",
                    path=f"profiles.{name}.num_predict",
                )
            )
        misplaced = OLLAMA_OPTIONS_RESERVED_KEYS.intersection(profile.options)
        if misplaced:
            issues.append(
                ValidationIssue(
                    code="reserved_option_key",
                    message=(
                        f"Profile {name!r} must set {sorted(misplaced)!r} as profile "
                        "fields, not inside options."
                    ),
                    path=f"profiles.{name}.options",
                )
            )
    for route_key, profile_name in profile_set.type_routes.items():
        if route_key not in known_types and route_key not in ROUTING_RESERVED_KEYS:
            issues.append(
                ValidationIssue(
                    code="unknown_newsletter_type",
                    message=f"Route key {route_key!r} is not a canonical classifier label.",
                    path=f"type_routes.{route_key}",
                )
            )
        if profile_name not in profile_set.profiles:
            issues.append(
                ValidationIssue(
                    code="unknown_profile_reference",
                    message=f"Route {route_key!r} references unknown profile {profile_name!r}.",
                    path=f"type_routes.{route_key}",
                )
            )
        else:
            profile = profile_set.profiles[profile_name]
            if not profile.enabled:
                issues.append(
                    _disabled_profile_issue(f"type_routes.{route_key}", profile_name)
                )
    return issues


def require_valid_summary_profile_set(
    profile_set: SummaryProfileSet,
    known_newsletter_types: tuple[str, ...] | None = None,
) -> SummaryProfileSet:
    """Raise an error if the profile set is invalid."""
    issues = validate_summary_profile_set(profile_set, known_newsletter_types)
    if issues:
        summary = "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        raise SummaryConfigError(summary)
    return profile_set


def list_summary_profiles(profile_set: SummaryProfileSet) -> list[SummaryProfileInfo]:
    """Return UI-friendly summary profile rows."""
    return [
        SummaryProfileInfo(
            name=profile.name,
            provider=profile.provider,
            model=profile.model,
            prompt_style=profile.prompt_style,
            temperature=profile.temperature,
            num_ctx=profile.num_ctx,
            timeout_seconds=profile.timeout_seconds,
            num_predict=profile.num_predict,
            think=profile.think,
            enabled=profile.enabled,
            description=profile.description,
            created_by=profile.created_by,
            version=profile.version,
        )
        for _, profile in sorted(profile_set.profiles.items())
    ]


def list_type_routes(profile_set: SummaryProfileSet) -> list[TypeRouteInfo]:
    """Return UI-friendly route rows."""
    return [
        TypeRouteInfo(
            newsletter_type=newsletter_type,
            profile_name=profile_name,
            is_reserved_key=newsletter_type in ROUTING_RESERVED_KEYS,
        )
        for newsletter_type, profile_name in sorted(profile_set.type_routes.items())
    ]


def export_summary_profile_set(
    profile_set: SummaryProfileSet, path: str | Path
) -> None:
    """Write a profile set to a JSON file."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(summary_profile_set_to_dict(profile_set), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def import_summary_profile_set(path: str | Path) -> SummaryProfileSet:
    """Read a profile set from a JSON file."""
    input_path = Path(path)
    if not input_path.is_file():
        raise SummaryConfigError(f"Summary profile set not found: {path}")
    try:
        data = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SummaryConfigError(
            f"Summary profile set {path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise SummaryConfigError("Summary profile set must be a JSON object.")
    return summary_profile_set_from_dict(data)
