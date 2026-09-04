"""Optional layered TOML user configuration for Rollup."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from rollup.effort import (
    EFFORT_COMPANION_KEYS,
    EFFORT_NAMES,
    EFFORT_OVERRIDE_KEYS,
    EFFORT_PROFILE_SLOTS,
    EffortModelOverride,
)
from rollup.folder_theme import FolderThemeOverride
from rollup.linkedin.config import LinkedInConfig, parse_linkedin_config
from rollup.reddit.config import RedditConfig, parse_reddit_config
from rollup.scholar.config import ScholarConfig, parse_scholar_config

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]


class UserConfigError(ValueError):
    """Invalid or unknown user configuration."""


# Sticky keys that may appear at the top level or inside [profiles.*].
STICKY_KEYS = frozenset(
    {
        "mail_root",
        "root",
        "output_dir",
        "state_dir",
        "log_dir",
        "lookback_days",
        "folder",
        "exclude_folder",
        "effort",
        "ollama",
        "ollama_model",
        "llm_provider",
        "llm_model",
        "summary_profile",
        "no_grouping",
        "grouping_min_size",
        "profile",
        "output",
    }
)

UI_KEYS = frozenset({"landing_page", "preferred_view", "onboarding_complete"})
UI_LANDING_PAGES = frozenset({"archive", "run", "settings"})
UI_PREFERRED_VIEWS = frozenset({"html", "markdown", "entries"})

TOP_LEVEL_KEYS = STICKY_KEYS | frozenset(
    {"folders", "profiles", "ui", "efforts", "linkedin", "reddit", "scholar"}
)

FOLDER_THEME_KEYS = frozenset({"emoji", "accent", "display_name", "order"})


@dataclass(frozen=True)
class UiPreferences:
    """Web UI preferences stored in TOML [ui], not SQLite."""

    landing_page: str = "archive"
    preferred_view: str = "html"
    onboarding_complete: bool = False


@dataclass(frozen=True)
class LoadedUserConfig:
    """Merged user config from one or more TOML files."""

    values: dict[str, Any] = field(default_factory=dict)
    folder_themes: dict[str, FolderThemeOverride] = field(default_factory=dict)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    efforts: dict[str, EffortModelOverride] = field(default_factory=dict)
    ui: UiPreferences = field(default_factory=UiPreferences)
    linkedin: LinkedInConfig = field(default_factory=LinkedInConfig)
    reddit: RedditConfig = field(default_factory=RedditConfig)
    scholar: ScholarConfig = field(default_factory=ScholarConfig)
    sources: tuple[Path, ...] = ()

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def has(self, key: str) -> bool:
        return key in self.values


def default_config_search_paths() -> tuple[Path, ...]:
    return (
        Path.home() / ".config" / "rollup" / "config.toml",
        Path.cwd() / "rollup.toml",
    )


def _as_str_list(value: Any, *, key: str, path: Path) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise UserConfigError(
        f"{path}: {key!r} must be a string or list of strings"
    )


def _parse_folder_themes(
    raw: Any, *, path: Path
) -> dict[str, FolderThemeOverride]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise UserConfigError(f"{path}: [folders] must be a table")
    themes: dict[str, FolderThemeOverride] = {}
    for slug, body in raw.items():
        if not isinstance(slug, str) or not slug.strip():
            raise UserConfigError(f"{path}: folder theme keys must be non-empty strings")
        if not isinstance(body, dict):
            raise UserConfigError(f"{path}: [folders.{slug}] must be a table")
        unknown = set(body) - FOLDER_THEME_KEYS
        if unknown:
            keys = ", ".join(sorted(unknown))
            raise UserConfigError(
                f"{path}: unknown key(s) in [folders.{slug}]: {keys}"
            )
        emoji = body.get("emoji")
        accent = body.get("accent")
        display_name = body.get("display_name")
        order = body.get("order")
        if emoji is not None and not isinstance(emoji, str):
            raise UserConfigError(f"{path}: [folders.{slug}].emoji must be a string")
        if accent is not None and not isinstance(accent, str):
            raise UserConfigError(f"{path}: [folders.{slug}].accent must be a string")
        if display_name is not None and not isinstance(display_name, str):
            raise UserConfigError(
                f"{path}: [folders.{slug}].display_name must be a string"
            )
        if order is not None and (
            not isinstance(order, int) or isinstance(order, bool)
        ):
            raise UserConfigError(
                f"{path}: [folders.{slug}].order must be an integer"
            )
        themes[slug.strip().lower()] = FolderThemeOverride(
            emoji=emoji,
            accent=accent,
            display_name=display_name,
            order=order,
        )
    return themes


def _parse_ui(raw: Any, *, path: Path) -> UiPreferences:
    if raw is None:
        return UiPreferences()
    if not isinstance(raw, dict):
        raise UserConfigError(f"{path}: [ui] must be a table")
    unknown = set(raw) - UI_KEYS
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise UserConfigError(f"{path}: unknown key(s) in [ui]: {keys}")
    landing = raw.get("landing_page", "archive")
    preferred = raw.get("preferred_view", "html")
    complete = raw.get("onboarding_complete", False)
    if not isinstance(landing, str) or landing not in UI_LANDING_PAGES:
        raise UserConfigError(
            f"{path}: [ui].landing_page must be one of "
            f"{', '.join(sorted(UI_LANDING_PAGES))}"
        )
    if not isinstance(preferred, str) or preferred not in UI_PREFERRED_VIEWS:
        raise UserConfigError(
            f"{path}: [ui].preferred_view must be one of "
            f"{', '.join(sorted(UI_PREFERRED_VIEWS))}"
        )
    if not isinstance(complete, bool):
        raise UserConfigError(f"{path}: [ui].onboarding_complete must be a boolean")
    return UiPreferences(
        landing_page=landing,
        preferred_view=preferred,
        onboarding_complete=complete,
    )


def _normalize_sticky(
    raw: Mapping[str, Any], *, path: Path, context: str
) -> dict[str, Any]:
    unknown = set(raw) - STICKY_KEYS
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise UserConfigError(f"{path}: unknown key(s) in {context}: {keys}")

    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key in {"mail_root", "root", "output_dir", "state_dir", "log_dir"}:
            if not isinstance(value, str) or not value.strip():
                raise UserConfigError(f"{path}: {key!r} must be a non-empty string")
            out[key] = value
        elif key == "lookback_days":
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise UserConfigError(
                    f"{path}: lookback_days must be a positive integer"
                )
            out[key] = value
        elif key in {"folder", "exclude_folder"}:
            out[key] = _as_str_list(value, key=key, path=path)
        elif key == "effort":
            if not isinstance(value, str) or not value.strip():
                raise UserConfigError(f"{path}: effort must be a non-empty string")
            out[key] = value.strip()
        elif key == "profile":
            if not isinstance(value, str) or not value.strip():
                raise UserConfigError(f"{path}: profile must be a non-empty string")
            out[key] = value.strip()
        elif key == "ollama_model":
            if not isinstance(value, str) or not value.strip():
                raise UserConfigError(
                    f"{path}: ollama_model must be a non-empty string"
                )
            out[key] = value.strip()
        elif key == "llm_provider":
            if not isinstance(value, str) or not value.strip():
                raise UserConfigError(f"{path}: llm_provider must be a non-empty string")
            provider = value.strip().lower()
            if provider not in {"ollama", "litellm"}:
                raise UserConfigError(
                    f"{path}: llm_provider must be 'ollama' or 'litellm'"
                )
            out[key] = provider
        elif key == "llm_model":
            if not isinstance(value, str) or not value.strip():
                raise UserConfigError(f"{path}: llm_model must be a non-empty string")
            out[key] = value.strip()
        elif key == "summary_profile":
            if not isinstance(value, str) or not value.strip():
                raise UserConfigError(
                    f"{path}: summary_profile must be a non-empty string"
                )
            out[key] = value.strip()
        elif key == "ollama":
            if not isinstance(value, bool):
                raise UserConfigError(f"{path}: ollama must be a boolean")
            out[key] = value
        elif key == "no_grouping":
            if not isinstance(value, bool):
                raise UserConfigError(f"{path}: no_grouping must be a boolean")
            out[key] = value
        elif key == "grouping_min_size":
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise UserConfigError(
                    f"{path}: grouping_min_size must be a positive integer"
                )
            out[key] = value
        elif key == "output":
            if isinstance(value, str):
                cleaned = value.strip().lower()
                if not cleaned:
                    raise UserConfigError(f"{path}: output must be non-empty")
                out[key] = [cleaned]
            elif isinstance(value, list) and all(isinstance(v, str) for v in value):
                cleaned_list = [v.strip().lower() for v in value if v.strip()]
                if not cleaned_list:
                    raise UserConfigError(
                        f"{path}: output must be a non-empty string or list of strings"
                    )
                out[key] = cleaned_list
            else:
                raise UserConfigError(
                    f"{path}: output must be a string or list of strings "
                    "(writer names, 'all', or 'none')"
                )
    return out


def _parse_profiles(raw: Any, *, path: Path) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise UserConfigError(f"{path}: [profiles] must be a table")
    profiles: dict[str, dict[str, Any]] = {}
    for name, body in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise UserConfigError(f"{path}: profile names must be non-empty strings")
        if not isinstance(body, dict):
            raise UserConfigError(f"{path}: [profiles.{name}] must be a table")
        profiles[name.strip()] = _normalize_sticky(
            body, path=path, context=f"[profiles.{name}]"
        )
    return profiles


def _parse_efforts(
    raw: Any, *, path: Path
) -> dict[str, EffortModelOverride]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise UserConfigError(f"{path}: [efforts] must be a table")
    out: dict[str, EffortModelOverride] = {}
    for name, body in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise UserConfigError(f"{path}: effort names must be non-empty strings")
        effort_name = name.strip()
        if effort_name not in EFFORT_NAMES:
            raise UserConfigError(
                f"{path}: unknown effort {effort_name!r} in [efforts]; "
                f"expected {', '.join(EFFORT_NAMES)}"
            )
        if not isinstance(body, dict):
            raise UserConfigError(f"{path}: [efforts.{effort_name}] must be a table")
        unknown = set(body) - EFFORT_OVERRIDE_KEYS
        if unknown:
            keys = ", ".join(sorted(unknown))
            raise UserConfigError(
                f"{path}: unknown key(s) in [efforts.{effort_name}]: {keys}"
            )
        profiles: dict[str, str] = {}
        for slot in EFFORT_PROFILE_SLOTS:
            if slot not in body:
                continue
            value = body[slot]
            if not isinstance(value, str) or not value.strip():
                raise UserConfigError(
                    f"{path}: [efforts.{effort_name}].{slot} must be a non-empty string"
                )
            profiles[slot] = value.strip()
        companions: dict[str, str | None] = {
            "ollama_model": None,
            "final_review_model": None,
        }
        for key in EFFORT_COMPANION_KEYS:
            if key not in body:
                continue
            value = body[key]
            if not isinstance(value, str) or not value.strip():
                raise UserConfigError(
                    f"{path}: [efforts.{effort_name}].{key} must be a non-empty string"
                )
            companions[key] = value.strip()
        override = EffortModelOverride(
            profiles=profiles,
            ollama_model=companions["ollama_model"],
            final_review_model=companions["final_review_model"],
        )
        if not override.is_empty():
            out[effort_name] = override
    return out


def efforts_to_raw(
    overrides: Mapping[str, EffortModelOverride],
) -> dict[str, dict[str, str]]:
    """Serialize effort overrides for TOML validation / round-trip."""
    out: dict[str, dict[str, str]] = {}
    for name, override in overrides.items():
        body: dict[str, str] = dict(override.profiles)
        if override.ollama_model:
            body["ollama_model"] = override.ollama_model
        if override.final_review_model:
            body["final_review_model"] = override.final_review_model
        if body:
            out[name] = body
    return out


def _merge_effort_overrides(
    base: Mapping[str, EffortModelOverride],
    overlay: Mapping[str, EffortModelOverride],
) -> dict[str, EffortModelOverride]:
    merged = dict(base)
    for name, override in overlay.items():
        existing = merged.get(name)
        if existing is None:
            merged[name] = override
            continue
        profiles = dict(existing.profiles)
        profiles.update(override.profiles)
        merged[name] = EffortModelOverride(
            profiles=profiles,
            ollama_model=(
                override.ollama_model
                if override.ollama_model is not None
                else existing.ollama_model
            ),
            final_review_model=(
                override.final_review_model
                if override.final_review_model is not None
                else existing.final_review_model
            ),
        )
    return merged


def parse_toml_dict(data: Mapping[str, Any], *, path: Path) -> LoadedUserConfig:
    unknown = set(data) - TOP_LEVEL_KEYS
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise UserConfigError(f"{path}: unknown top-level key(s): {keys}")

    sticky_raw = {k: v for k, v in data.items() if k in STICKY_KEYS}
    values = _normalize_sticky(sticky_raw, path=path, context="top level")
    folder_themes = _parse_folder_themes(data.get("folders"), path=path)
    profiles = _parse_profiles(data.get("profiles"), path=path)
    efforts = _parse_efforts(data.get("efforts"), path=path)
    ui = _parse_ui(data.get("ui"), path=path)
    try:
        linkedin = parse_linkedin_config(data.get("linkedin"), path=path)
    except ValueError as exc:
        raise UserConfigError(str(exc)) from exc
    try:
        reddit = parse_reddit_config(data.get("reddit"), path=path)
    except ValueError as exc:
        raise UserConfigError(str(exc)) from exc
    try:
        scholar = parse_scholar_config(data.get("scholar"), path=path)
    except ValueError as exc:
        raise UserConfigError(str(exc)) from exc
    return LoadedUserConfig(
        values=values,
        folder_themes=folder_themes,
        profiles=profiles,
        efforts=efforts,
        ui=ui,
        linkedin=linkedin,
        reddit=reddit,
        scholar=scholar,
        sources=(path,),
    )


def load_toml_file(path: Path) -> LoadedUserConfig:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UserConfigError(f"Cannot read config {path}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise UserConfigError(f"Invalid TOML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise UserConfigError(f"{path}: root must be a table")
    return parse_toml_dict(data, path=path)


def _merge_loaded(base: LoadedUserConfig, overlay: LoadedUserConfig) -> LoadedUserConfig:
    values = dict(base.values)
    values.update(overlay.values)
    folder_themes = dict(base.folder_themes)
    folder_themes.update(overlay.folder_themes)
    profiles = dict(base.profiles)
    for name, body in overlay.profiles.items():
        merged = dict(profiles.get(name, {}))
        merged.update(body)
        profiles[name] = merged
    # Later file wins for [ui] wholesale (same as sticky values).
    ui = overlay.ui if overlay.sources else base.ui
    linkedin = overlay.linkedin if overlay.sources else base.linkedin
    reddit = overlay.reddit if overlay.sources else base.reddit
    scholar = overlay.scholar if overlay.sources else base.scholar
    return LoadedUserConfig(
        values=values,
        folder_themes=folder_themes,
        profiles=profiles,
        efforts=_merge_effort_overrides(base.efforts, overlay.efforts),
        ui=ui,
        linkedin=linkedin,
        reddit=reddit,
        scholar=scholar,
        sources=tuple(base.sources) + tuple(overlay.sources),
    )


def load_user_config(
    *,
    explicit_path: str | Path | None = None,
    search_paths: tuple[Path, ...] | None = None,
) -> LoadedUserConfig:
    """Load and merge user config. Later files win. Missing files are skipped."""
    if explicit_path is not None:
        path = Path(explicit_path).expanduser()
        if not path.is_file():
            raise UserConfigError(f"Config file not found: {path}")
        return load_toml_file(path)

    merged = LoadedUserConfig()
    for path in search_paths if search_paths is not None else default_config_search_paths():
        if path.is_file():
            merged = _merge_loaded(merged, load_toml_file(path))
    return merged


def extract_config_path(argv: list[str]) -> tuple[str | None, list[str]]:
    """Pull --config PATH from argv; return (path, remaining argv)."""
    out: list[str] = []
    config_path: str | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--config":
            if i + 1 >= len(argv):
                raise UserConfigError("--config requires a path argument")
            config_path = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--config="):
            config_path = arg.split("=", 1)[1]
            i += 1
            continue
        out.append(arg)
        i += 1
    return config_path, out


def flag_present(argv: list[str], flag: str) -> bool:
    """True if flag or flag=value appears in argv."""
    prefix = f"{flag}="
    return any(a == flag or a.startswith(prefix) for a in argv)


def apply_sticky_to_namespace(
    args: Any,
    sticky: Mapping[str, Any],
    argv: list[str],
) -> None:
    """Apply sticky config onto argparse namespace where CLI did not set the flag."""
    from rollup.sticky_flags import apply_sticky_specs

    apply_sticky_specs(args, sticky, argv)

