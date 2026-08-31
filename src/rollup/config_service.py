"""Shared configuration service: load, validate, preview, and atomically save TOML."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import tomlkit
from tomlkit.items import Table

from rollup.effort import EFFORT_NAMES, EffortModelOverride
from rollup.folder_theme import FolderThemeOverride
from rollup.linkedin.config import LinkedInConfig, LinkedInSearch
from rollup.run_profiles import (
    DEFAULT_RUN_PROFILE,
    UnknownRunProfileError,
    resolve_run_profile,
)
from rollup.safety import SafetyError, validate_writable_run_paths
from rollup.user_config import (
    FOLDER_THEME_KEYS,
    STICKY_KEYS,
    UI_KEYS,
    UI_LANDING_PAGES,
    UI_PREFERRED_VIEWS,
    LoadedUserConfig,
    UiPreferences,
    UserConfigError,
    default_config_search_paths,
    load_user_config,
    parse_toml_dict,
    efforts_to_raw,
)


class ConfigConflictError(UserConfigError):
    """Optimistic concurrency failure — file changed since the editor loaded it."""


class ConfigValidationError(UserConfigError):
    """Validation failed for a proposed configuration patch."""


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    message: str
    severity: str = "error"  # error | warning


@dataclass(frozen=True)
class ConfigDocument:
    """Editable primary config file plus parsed view and concurrency token."""

    path: Path
    loaded: LoadedUserConfig
    revision: str
    exists: bool
    search_conflict: bool = False
    conflicting_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class EffectiveConfigView:
    """Resolved sticky merge for a profile (builtins → files → profile → overrides)."""

    profile_name: str
    sticky: dict[str, Any]
    folder_themes: dict[str, FolderThemeOverride]
    ui: UiPreferences
    sources: tuple[Path, ...]
    ollama_contacted: bool
    writers: list[str]
    effort_overrides: dict[str, EffortModelOverride] = field(default_factory=dict)
    linkedin: LinkedInConfig = field(default_factory=LinkedInConfig)


@dataclass
class ConfigPatch:
    """Proposed edits to sticky values, folders, profiles, and UI prefs."""

    values: dict[str, Any] = field(default_factory=dict)
    clear_values: set[str] = field(default_factory=set)
    folder_themes: dict[str, FolderThemeOverride] | None = None
    profiles: dict[str, dict[str, Any]] | None = None
    remove_profiles: set[str] = field(default_factory=set)
    ui: UiPreferences | None = None
    effort_overrides: dict[str, EffortModelOverride] | None = None
    linkedin: LinkedInConfig | None = None


def compute_revision(path: Path) -> str:
    """Content hash + mtime for optimistic concurrency."""
    if not path.is_file():
        return "missing"
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    st = path.stat()
    return f"{digest}:{st.st_mtime_ns}:{st.st_size}"


def resolve_config_path(*, explicit: str | Path | None = None) -> Path:
    """Primary write/read path: --config, else cwd rollup.toml, else XDG."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    cwd = Path.cwd() / "rollup.toml"
    if cwd.is_file():
        return cwd.resolve()
    return (Path.home() / ".config" / "rollup" / "config.toml").resolve()


def load_document(
    *,
    explicit: str | Path | None = None,
    search_paths: tuple[Path, ...] | None = None,
) -> ConfigDocument:
    """Load the primary editable document and merged sticky view."""
    path = resolve_config_path(explicit=explicit)
    search = search_paths if search_paths is not None else default_config_search_paths()
    present = [p.resolve() for p in search if p.is_file()]
    conflict = explicit is None and len(present) > 1
    if path.is_file():
        loaded_file = load_user_config(explicit_path=path)
    else:
        loaded_file = LoadedUserConfig(sources=())
    # Merged view for effective resolve when using search paths.
    if explicit is not None:
        merged = loaded_file
    else:
        merged = load_user_config(search_paths=search)
    return ConfigDocument(
        path=path,
        loaded=merged if merged.sources else loaded_file,
        revision=compute_revision(path),
        exists=path.is_file(),
        search_conflict=conflict,
        conflicting_paths=tuple(present) if conflict else (),
    )


def resolve_effective(
    loaded: LoadedUserConfig,
    *,
    profile_name: str | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> EffectiveConfigView:
    """Same merge semantics as CLI sticky apply (without argparse flags)."""
    name = profile_name or loaded.values.get("profile") or DEFAULT_RUN_PROFILE
    try:
        profile = resolve_run_profile(str(name), toml_profiles=loaded.profiles)
    except UnknownRunProfileError:
        profile = resolve_run_profile(DEFAULT_RUN_PROFILE, toml_profiles=loaded.profiles)
        name = profile.name
    sticky: dict[str, Any] = {}
    sticky.update(loaded.values)
    sticky.pop("profile", None)
    sticky.update(profile.values)
    sticky.pop("profile", None)
    if overrides:
        for key, value in overrides.items():
            if key in STICKY_KEYS and key != "profile":
                sticky[key] = value
    ollama_on = bool(sticky.get("ollama"))
    writers = list(sticky.get("output") or ["all"])
    return EffectiveConfigView(
        profile_name=str(name),
        sticky=sticky,
        folder_themes=dict(loaded.folder_themes),
        ui=loaded.ui,
        sources=loaded.sources,
        ollama_contacted=ollama_on,
        writers=writers,
        effort_overrides=dict(loaded.efforts),
        linkedin=loaded.linkedin,
    )


def validate_paths_for_sticky(sticky: Mapping[str, Any]) -> list[ValidationIssue]:
    """Containment checks when enough path keys are present."""
    issues: list[ValidationIssue] = []
    root = sticky.get("root")
    mail_root = sticky.get("mail_root")
    output_dir = sticky.get("output_dir")
    state_dir = sticky.get("state_dir")
    log_dir = sticky.get("log_dir")
    if not root or not mail_root:
        if root and not mail_root:
            issues.append(
                ValidationIssue("mail_root", "mail_root is required when root is set")
            )
        return issues
    try:
        newsletter = Path(str(root)).expanduser()
        mail = Path(str(mail_root)).expanduser()
        from rollup.config import DEFAULT_OUTPUT_DIR, DEFAULT_STATE_DIR

        out = Path(str(output_dir or DEFAULT_OUTPUT_DIR)).expanduser()
        state = Path(str(state_dir or DEFAULT_STATE_DIR)).expanduser()
        logs = Path(str(log_dir or "./logs")).expanduser()
        validate_writable_run_paths(
            newsletter_root=newsletter,
            mail_root=mail,
            output_dir=out,
            state_dir=state,
            log_dir=logs,
        )
    except SafetyError as exc:
        issues.append(ValidationIssue("paths", str(exc)))
    except OSError as exc:
        issues.append(ValidationIssue("paths", f"Path check failed: {exc}"))
    return issues


def validate_patch(patch: ConfigPatch, *, base: LoadedUserConfig) -> list[ValidationIssue]:
    """Validate a patch against the sticky/folder/profile/ui schema."""
    issues: list[ValidationIssue] = []
    proposed_values = dict(base.values)
    for key in patch.clear_values:
        proposed_values.pop(key, None)
    proposed_values.update(patch.values)

    try:
        parse_toml_dict(
            {
                **{k: v for k, v in proposed_values.items() if k in STICKY_KEYS},
                "folders": _themes_to_raw(
                    patch.folder_themes
                    if patch.folder_themes is not None
                    else base.folder_themes
                ),
                "profiles": _profiles_after_patch(base.profiles, patch),
                "ui": _ui_to_raw(patch.ui if patch.ui is not None else base.ui),
                "efforts": efforts_to_raw(
                    patch.effort_overrides
                    if patch.effort_overrides is not None
                    else base.efforts
                ),
                "linkedin": _linkedin_to_raw(
                    patch.linkedin if patch.linkedin is not None else base.linkedin
                ),
            },
            path=Path("<patch>"),
        )
    except UserConfigError as exc:
        issues.append(ValidationIssue("schema", str(exc)))
        return issues

    sticky_for_paths = dict(proposed_values)
    if patch.profiles is not None or patch.remove_profiles:
        # Path validation uses top-level sticky only.
        pass
    issues.extend(validate_paths_for_sticky(sticky_for_paths))
    return issues


def _themes_to_raw(
    themes: Mapping[str, FolderThemeOverride],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for slug, theme in themes.items():
        body: dict[str, Any] = {}
        if theme.emoji is not None:
            body["emoji"] = theme.emoji
        if theme.accent is not None:
            body["accent"] = theme.accent
        if theme.display_name is not None:
            body["display_name"] = theme.display_name
        if theme.order is not None:
            body["order"] = theme.order
        out[slug] = body
    return out


def _linkedin_to_raw(linkedin: LinkedInConfig) -> dict[str, Any]:
    body: dict[str, Any] = {"enabled": linkedin.enabled}
    if linkedin.searches:
        searches: dict[str, dict[str, Any]] = {}
        for slug, search in sorted(linkedin.searches.items()):
            row: dict[str, Any] = {"url": search.url, "enabled": search.enabled}
            if search.display_name:
                row["display_name"] = search.display_name
            if search.emoji:
                row["emoji"] = search.emoji
            if search.accent:
                row["accent"] = search.accent
            if search.order is not None:
                row["order"] = search.order
            searches[slug] = row
        body["searches"] = searches
    return body


def _ui_to_raw(ui: UiPreferences) -> dict[str, Any]:
    return {
        "landing_page": ui.landing_page,
        "preferred_view": ui.preferred_view,
        "onboarding_complete": ui.onboarding_complete,
    }


def _profiles_after_patch(
    base: Mapping[str, dict[str, Any]],
    patch: ConfigPatch,
) -> dict[str, dict[str, Any]]:
    profiles = {k: dict(v) for k, v in base.items()}
    for name in patch.remove_profiles:
        profiles.pop(name, None)
    if patch.profiles is not None:
        for name, body in patch.profiles.items():
            profiles[name] = dict(body)
    return profiles


def effective_diff(
    before: EffectiveConfigView,
    after: EffectiveConfigView,
) -> list[tuple[str, str, str]]:
    """Return (key, old, new) rows that changed."""
    keys = sorted(set(before.sticky) | set(after.sticky) | {"profile", "ollama_contacted"})
    rows: list[tuple[str, str, str]] = []
    for key in keys:
        if key == "profile":
            old, new = before.profile_name, after.profile_name
        elif key == "ollama_contacted":
            old = "yes" if before.ollama_contacted else "no"
            new = "yes" if after.ollama_contacted else "no"
        else:
            old = _fmt(before.sticky.get(key))
            new = _fmt(after.sticky.get(key))
        if old != new:
            rows.append((key, old, new))
    effort_keys = sorted(
        set(_flatten_effort_overrides(before.effort_overrides))
        | set(_flatten_effort_overrides(after.effort_overrides))
    )
    before_eff = _flatten_effort_overrides(before.effort_overrides)
    after_eff = _flatten_effort_overrides(after.effort_overrides)
    for key in effort_keys:
        old = before_eff.get(key, "(unset)")
        new = after_eff.get(key, "(unset)")
        if old != new:
            rows.append((key, old, new))
    return rows


def _flatten_effort_overrides(
    overrides: Mapping[str, EffortModelOverride],
) -> dict[str, str]:
    flat: dict[str, str] = {}
    for name, override in overrides.items():
        for slot, model in override.profiles.items():
            flat[f"efforts.{name}.{slot}"] = model
        if override.ollama_model:
            flat[f"efforts.{name}.ollama_model"] = override.ollama_model
        if override.final_review_model:
            flat[f"efforts.{name}.final_review_model"] = override.final_review_model
    return flat


def _fmt(value: Any) -> str:
    if value is None:
        return "(unset)"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "(empty)"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def apply_and_save(
    path: Path,
    patch: ConfigPatch,
    *,
    expected_revision: str,
    base_loaded: LoadedUserConfig | None = None,
    backup_dir: Path | None = None,
) -> ConfigDocument:
    """Validate, backup, atomically write TOML; raise on conflict or validation."""
    path = Path(path).expanduser()
    current_rev = compute_revision(path)
    if current_rev != expected_revision:
        raise ConfigConflictError(
            f"Config changed on disk (expected revision {expected_revision[:16]}…, "
            f"found {current_rev[:16]}…)"
        )

    if path.is_file():
        file_loaded = load_user_config(explicit_path=path)
    else:
        file_loaded = base_loaded or LoadedUserConfig()

    issues = validate_patch(patch, base=file_loaded)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise ConfigValidationError("; ".join(f"{i.field}: {i.message}" for i in errors))

    if path.is_file():
        text = path.read_text(encoding="utf-8")
        try:
            doc = tomlkit.parse(text)
        except Exception as exc:
            raise UserConfigError(f"Invalid TOML in {path}: {exc}") from exc
    else:
        doc = tomlkit.document()

    _apply_patch_to_doc(doc, patch, file_loaded)

    rendered = tomlkit.dumps(doc)
    parsed = parse_toml_dict(_loads_plain_toml(rendered), path=path)
    _atomic_write_with_backup(
        path,
        rendered.encode("utf-8"),
        backup_dir=backup_dir,
    )
    return ConfigDocument(
        path=path.resolve(),
        loaded=parsed,
        revision=compute_revision(path),
        exists=True,
    )


def _loads_plain_toml(text: str) -> dict[str, Any]:
    import sys

    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore[no-redef]
    data = tomllib.loads(text)
    if not isinstance(data, dict):
        raise UserConfigError("TOML root must be a table")
    return data


def _apply_patch_to_doc(
    doc: Any,
    patch: ConfigPatch,
    base: LoadedUserConfig,
) -> None:
    for key in patch.clear_values:
        if key in doc:
            del doc[key]
    for key, value in patch.values.items():
        if key not in STICKY_KEYS:
            continue
        doc[key] = _to_toml_value(value)

    if patch.folder_themes is not None:
        folders = tomlkit.table()
        for slug, theme in sorted(patch.folder_themes.items()):
            body = tomlkit.table()
            if theme.emoji is not None:
                body["emoji"] = theme.emoji
            if theme.accent is not None:
                body["accent"] = theme.accent
            if theme.display_name is not None:
                body["display_name"] = theme.display_name
            if theme.order is not None:
                body["order"] = theme.order
            if body:
                folders[slug] = body
        if folders:
            doc["folders"] = folders
        elif "folders" in doc:
            del doc["folders"]

    if patch.profiles is not None or patch.remove_profiles:
        profiles_table: Table
        if "profiles" in doc and isinstance(doc["profiles"], Table):
            profiles_table = doc["profiles"]
        else:
            profiles_table = tomlkit.table()
            doc["profiles"] = profiles_table
        for name in patch.remove_profiles:
            if name in profiles_table:
                del profiles_table[name]
        if patch.profiles is not None:
            for name, body in patch.profiles.items():
                inner = tomlkit.table()
                for k, v in body.items():
                    if k in STICKY_KEYS and k != "profile":
                        inner[k] = _to_toml_value(v)
                profiles_table[name] = inner

    if patch.ui is not None:
        ui_table = tomlkit.table()
        ui_table["landing_page"] = patch.ui.landing_page
        ui_table["preferred_view"] = patch.ui.preferred_view
        ui_table["onboarding_complete"] = patch.ui.onboarding_complete
        doc["ui"] = ui_table

    if patch.effort_overrides is not None:
        raw = efforts_to_raw(patch.effort_overrides)
        if raw:
            efforts_table = tomlkit.table()
            for name in EFFORT_NAMES:
                body_raw = raw.get(name)
                if not body_raw:
                    continue
                body = tomlkit.table()
                for key, value in body_raw.items():
                    body[key] = value
                efforts_table[name] = body
            doc["efforts"] = efforts_table
        elif "efforts" in doc:
            del doc["efforts"]

    if patch.linkedin is not None:
        linkedin_table = tomlkit.table()
        linkedin_table["enabled"] = patch.linkedin.enabled
        if patch.linkedin.searches:
            searches_table = tomlkit.table()
            for slug, search in sorted(patch.linkedin.searches.items()):
                row = tomlkit.table()
                row["url"] = search.url
                row["enabled"] = search.enabled
                if search.display_name:
                    row["display_name"] = search.display_name
                if search.emoji:
                    row["emoji"] = search.emoji
                if search.accent:
                    row["accent"] = search.accent
                if search.order is not None:
                    row["order"] = search.order
                searches_table[slug] = row
            linkedin_table["searches"] = searches_table
        doc["linkedin"] = linkedin_table
    elif patch.linkedin is None and "linkedin" in doc and patch.clear_values:
        pass


def _to_toml_value(value: Any) -> Any:
    if isinstance(value, list):
        arr = tomlkit.array()
        for item in value:
            arr.append(item)
        return arr
    return value


def _atomic_write_with_backup(
    path: Path,
    data: bytes,
    *,
    backup_dir: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        if backup_dir is not None:
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            stamped = backup_dir / f"{path.name}.{stamp}.bak"
            shutil.copy2(path, stamped)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def patch_from_form_values(
    *,
    mail_root: str | None = None,
    root: str | None = None,
    output_dir: str | None = None,
    state_dir: str | None = None,
    log_dir: str | None = None,
    lookback_days: int | None = None,
    folder: list[str] | None = None,
    exclude_folder: list[str] | None = None,
    effort: str | None = None,
    ollama: bool | None = None,
    ollama_model: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    summary_profile: str | None = None,
    no_grouping: bool | None = None,
    grouping_min_size: int | None = None,
    profile: str | None = None,
    output: list[str] | None = None,
    folder_themes: dict[str, FolderThemeOverride] | None = None,
    profiles: dict[str, dict[str, Any]] | None = None,
    remove_profiles: set[str] | None = None,
    ui: UiPreferences | None = None,
    effort_overrides: dict[str, EffortModelOverride] | None = None,
    linkedin: LinkedInConfig | None = None,
) -> ConfigPatch:
    """Build a ConfigPatch from optional form fields (None = leave unchanged)."""
    values: dict[str, Any] = {}
    clear: set[str] = set()

    def _set_str(key: str, raw: str | None) -> None:
        if raw is None:
            return
        cleaned = raw.strip()
        if not cleaned:
            clear.add(key)
        else:
            values[key] = cleaned

    _set_str("mail_root", mail_root)
    _set_str("root", root)
    _set_str("output_dir", output_dir)
    _set_str("state_dir", state_dir)
    _set_str("log_dir", log_dir)
    _set_str("effort", effort)
    _set_str("ollama_model", ollama_model)
    _set_str("llm_provider", llm_provider)
    _set_str("llm_model", llm_model)
    _set_str("summary_profile", summary_profile)
    _set_str("profile", profile)

    if lookback_days is not None:
        values["lookback_days"] = lookback_days
    if folder is not None:
        if folder:
            values["folder"] = folder
        else:
            clear.add("folder")
    if exclude_folder is not None:
        if exclude_folder:
            values["exclude_folder"] = exclude_folder
        else:
            clear.add("exclude_folder")
    if ollama is not None:
        values["ollama"] = ollama
    if no_grouping is not None:
        values["no_grouping"] = no_grouping
    if grouping_min_size is not None:
        values["grouping_min_size"] = grouping_min_size
    if output is not None:
        values["output"] = output if output else ["all"]

    return ConfigPatch(
        values=values,
        clear_values=clear,
        folder_themes=folder_themes,
        profiles=profiles,
        remove_profiles=remove_profiles or set(),
        ui=ui,
        effort_overrides=effort_overrides,
        linkedin=linkedin,
    )


def build_digest_argv(
    effective: EffectiveConfigView,
    *,
    config_path: Path | None = None,
    dry_run: bool = False,
    extra: list[str] | None = None,
) -> list[str]:
    """Build `rollup digest` argv from an effective view (for Run Studio / display)."""
    from rollup.sticky_flags import sticky_to_argv

    argv: list[str] = ["digest"]
    if config_path is not None:
        argv = ["--config", str(config_path), "digest"]
    argv.extend(["--profile", effective.profile_name])
    argv.extend(sticky_to_argv(effective.sticky))
    if effective.linkedin.enabled:
        argv.append("--linkedin")
    else:
        argv.append("--no-linkedin")
    if dry_run:
        argv.append("--dry-run")
    if extra:
        argv.extend(extra)
    return argv


# Re-export constants useful for web forms.
__all__ = [
    "ConfigConflictError",
    "ConfigDocument",
    "ConfigPatch",
    "ConfigValidationError",
    "EffectiveConfigView",
    "ValidationIssue",
    "apply_and_save",
    "build_digest_argv",
    "compute_revision",
    "effective_diff",
    "load_document",
    "patch_from_form_values",
    "resolve_config_path",
    "resolve_effective",
    "validate_patch",
    "validate_paths_for_sticky",
    "FOLDER_THEME_KEYS",
    "STICKY_KEYS",
    "UI_KEYS",
    "UI_LANDING_PAGES",
    "UI_PREFERRED_VIEWS",
]
