"""Optional layered TOML user configuration for Rollup."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from rollup.folder_theme import FolderThemeOverride

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
        "no_grouping",
        "grouping_min_size",
        "profile",
        "output",
    }
)

TOP_LEVEL_KEYS = STICKY_KEYS | frozenset({"folders", "profiles"})

FOLDER_THEME_KEYS = frozenset({"emoji", "accent"})


@dataclass(frozen=True)
class LoadedUserConfig:
    """Merged user config from one or more TOML files."""

    values: dict[str, Any] = field(default_factory=dict)
    folder_themes: dict[str, FolderThemeOverride] = field(default_factory=dict)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
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
        if emoji is not None and not isinstance(emoji, str):
            raise UserConfigError(f"{path}: [folders.{slug}].emoji must be a string")
        if accent is not None and not isinstance(accent, str):
            raise UserConfigError(f"{path}: [folders.{slug}].accent must be a string")
        themes[slug.strip().lower()] = FolderThemeOverride(
            emoji=emoji,
            accent=accent,
        )
    return themes


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


def parse_toml_dict(data: Mapping[str, Any], *, path: Path) -> LoadedUserConfig:
    unknown = set(data) - TOP_LEVEL_KEYS
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise UserConfigError(f"{path}: unknown top-level key(s): {keys}")

    sticky_raw = {k: v for k, v in data.items() if k in STICKY_KEYS}
    values = _normalize_sticky(sticky_raw, path=path, context="top level")
    folder_themes = _parse_folder_themes(data.get("folders"), path=path)
    profiles = _parse_profiles(data.get("profiles"), path=path)
    return LoadedUserConfig(
        values=values,
        folder_themes=folder_themes,
        profiles=profiles,
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
    return LoadedUserConfig(
        values=values,
        folder_themes=folder_themes,
        profiles=profiles,
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
    if "lookback_days" in sticky and not flag_present(argv, "--lookback-days"):
        args.lookback_days = sticky["lookback_days"]
    if "effort" in sticky and not flag_present(argv, "--effort"):
        args.effort = sticky["effort"]
    if "grouping_min_size" in sticky and not flag_present(
        argv, "--grouping-min-size"
    ):
        args.grouping_min_size = sticky["grouping_min_size"]

    if "folder" in sticky and not flag_present(argv, "--folder"):
        args.folder = list(sticky["folder"])
    if "exclude_folder" in sticky and not flag_present(argv, "--exclude-folder"):
        args.exclude_folder = list(sticky["exclude_folder"])

    path_map = {
        "root": "--root",
        "mail_root": "--mail-root",
        "output_dir": "--output-dir",
        "state_dir": "--state-dir",
        "log_dir": "--log-dir",
    }
    for key, flag in path_map.items():
        if key in sticky and not flag_present(argv, flag):
            setattr(args, key, sticky[key])

    # Grouping: only apply when neither --grouping nor --no-grouping was passed.
    if "no_grouping" in sticky and not (
        flag_present(argv, "--grouping") or flag_present(argv, "--no-grouping")
    ):
        if sticky["no_grouping"]:
            args.no_grouping = True
            args.grouping = False
        else:
            args.no_grouping = False

    # Ollama: only when neither opt-in nor opt-out flag was passed.
    if "ollama" in sticky and not (
        flag_present(argv, "--ollama") or flag_present(argv, "--no-ollama")
    ):
        if sticky["ollama"]:
            args.ollama = True
            args.no_ollama = False
        else:
            args.ollama = False
            args.no_ollama = True

    # Output writers: only when neither --output nor --xteink/--x3 was passed.
    if "output" in sticky and not (
        flag_present(argv, "--output")
        or flag_present(argv, "--xteink")
        or flag_present(argv, "--x3")
    ):
        values = list(sticky["output"])
        if values == ["all"]:
            # Empty list → default-all policy in requested_writer_names.
            args.output = []
        else:
            args.output = values

