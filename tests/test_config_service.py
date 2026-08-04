"""Tests for shared configuration service."""

from __future__ import annotations

from pathlib import Path

import pytest

from rollup.config_service import (
    ConfigConflictError,
    ConfigPatch,
    apply_and_save,
    build_digest_argv,
    compute_revision,
    patch_from_form_values,
    resolve_config_path,
    resolve_effective,
)
from rollup.folder_theme import FolderThemeOverride, folder_display_name, sort_folder_names
from rollup.user_config import UiPreferences, parse_toml_dict


def test_resolve_config_path_explicit(tmp_path: Path) -> None:
    p = tmp_path / "custom.toml"
    assert resolve_config_path(explicit=p) == p.resolve()


def test_resolve_prefers_cwd_rollup_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cwd_cfg = tmp_path / "rollup.toml"
    cwd_cfg.write_text("lookback_days = 3\n", encoding="utf-8")
    assert resolve_config_path() == cwd_cfg.resolve()


def test_save_atomic_and_backup(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('lookback_days = 7\neffort = "balanced"\n', encoding="utf-8")
    rev = compute_revision(path)
    backup_dir = tmp_path / "backups"
    patch = patch_from_form_values(lookback_days=3, effort="light")
    saved = apply_and_save(
        path, patch, expected_revision=rev, backup_dir=backup_dir
    )
    assert saved.loaded.values["lookback_days"] == 3
    assert saved.loaded.values["effort"] == "light"
    assert path.with_suffix(".toml.bak").is_file()
    assert any(backup_dir.iterdir())


def test_optimistic_concurrency(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("lookback_days = 7\n", encoding="utf-8")
    stale = "missing"
    with pytest.raises(ConfigConflictError):
        apply_and_save(
            path,
            patch_from_form_values(lookback_days=1),
            expected_revision=stale,
        )


def test_folder_display_name_and_order() -> None:
    themes = {
        "tech": FolderThemeOverride(
            emoji="💻", display_name="Technology", order=1
        ),
        "sports": FolderThemeOverride(order=2, emoji="🏀"),
    }
    assert folder_display_name("tech", themes) == "💻 Technology"
    assert folder_display_name("sports", themes) == "🏀 sports"
    assert sort_folder_names(["sports", "tech", "zzz"], themes) == [
        "tech",
        "sports",
        "zzz",
    ]


def test_ui_section_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("lookback_days = 7\n", encoding="utf-8")
    rev = compute_revision(path)
    patch = ConfigPatch(
        ui=UiPreferences(
            landing_page="run",
            preferred_view="markdown",
            onboarding_complete=True,
        )
    )
    saved = apply_and_save(path, patch, expected_revision=rev)
    assert saved.loaded.ui.landing_page == "run"
    assert saved.loaded.ui.onboarding_complete is True


def test_new_sticky_keys_parse(tmp_path: Path) -> None:
    cfg = parse_toml_dict(
        {
            "ollama_model": "llama3.2",
            "summary_profile": "standard",
            "folders": {
                "tech": {
                    "emoji": "💻",
                    "display_name": "Tech",
                    "order": 1,
                }
            },
            "ui": {"landing_page": "settings", "preferred_view": "entries"},
        },
        path=tmp_path / "x.toml",
    )
    assert cfg.values["ollama_model"] == "llama3.2"
    assert cfg.folder_themes["tech"].display_name == "Tech"
    assert cfg.ui.landing_page == "settings"


def test_build_digest_argv_includes_dry_run(tmp_path: Path) -> None:
    from rollup.user_config import LoadedUserConfig

    loaded = LoadedUserConfig(
        values={"lookback_days": 1, "ollama": False, "root": str(tmp_path)}
    )
    eff = resolve_effective(loaded, profile_name="daily")
    argv = build_digest_argv(eff, dry_run=True)
    assert "digest" in argv
    assert "--dry-run" in argv
    assert "--no-ollama" in argv
    assert "--profile" in argv


def test_build_digest_argv_fixture_paths() -> None:
    from rollup.user_config import LoadedUserConfig

    root = Path("tests/fixtures/Newsletters.sbd").resolve()
    mail = Path("tests/fixtures").resolve()
    loaded = LoadedUserConfig(
        values={
            "root": str(root),
            "mail_root": str(mail),
            "lookback_days": 3650,
            "ollama": False,
            "output": ["none"],
            "effort": "light",
        }
    )
    eff = resolve_effective(loaded, profile_name="weekly")
    argv = build_digest_argv(eff, dry_run=True)
    assert "--root" in argv
    assert str(root) in argv
    assert "--mail-root" in argv
    assert "--output" in argv and "none" in argv
    assert "--dry-run" in argv


def test_validate_paths_rejects_output_inside_mail(tmp_path: Path) -> None:
    from rollup.config_service import validate_paths_for_sticky

    mail = tmp_path / "mail"
    root = mail / "Newsletters.sbd"
    root.mkdir(parents=True)
    issues = validate_paths_for_sticky(
        {
            "root": str(root),
            "mail_root": str(mail),
            "output_dir": str(mail / "out"),
            "state_dir": str(tmp_path / "state"),
            "log_dir": str(tmp_path / "logs"),
        }
    )
    assert any(i.severity == "error" for i in issues)


def test_custom_profile_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("lookback_days = 7\n", encoding="utf-8")
    rev = compute_revision(path)
    patch = ConfigPatch(
        profiles={"tech-only": {"lookback_days": 3, "folder": ["tech"], "effort": "light"}}
    )
    saved = apply_and_save(path, patch, expected_revision=rev)
    assert saved.loaded.profiles["tech-only"]["folder"] == ["tech"]
    eff = resolve_effective(saved.loaded, profile_name="tech-only")
    assert eff.sticky["lookback_days"] == 3
    assert eff.sticky["folder"] == ["tech"]


def test_custom_profile_in_effective_merge() -> None:
    from rollup.user_config import LoadedUserConfig

    loaded = LoadedUserConfig(
        values={"profile": "tech"},
        profiles={"tech": {"lookback_days": 2, "folder": ["tech"], "effort": "light"}},
    )
    eff = resolve_effective(loaded)
    assert eff.profile_name == "tech"
    assert eff.sticky["lookback_days"] == 2
    assert eff.sticky["folder"] == ["tech"]


def test_sticky_flag_registry_covers_sticky_keys() -> None:
    from rollup.sticky_flags import assert_sticky_keys_covered

    assert_sticky_keys_covered()


def test_sticky_to_argv_apply_roundtrip(tmp_path: Path) -> None:
    """Flags from sticky_to_argv should be respected by apply (CLI wins)."""
    import argparse

    from rollup.sticky_flags import sticky_to_argv
    from rollup.user_config import apply_sticky_to_namespace

    sticky = {
        "lookback_days": 9,
        "root": str(tmp_path / "root"),
        "mail_root": str(tmp_path / "mail"),
        "effort": "light",
        "ollama": True,
        "ollama_model": "llama3.2",
        "no_grouping": True,
        "grouping_min_size": 4,
        "folder": ["tech"],
        "exclude_folder": ["noise"],
        "output": ["json", "txt"],
        "summary_profile": "brief",
    }
    emitted = sticky_to_argv(sticky)
    assert "--lookback-days" in emitted and "9" in emitted
    assert "--ollama" in emitted
    assert "--no-grouping" in emitted
    assert emitted.count("--folder") == 1
    assert emitted.count("--output") == 2

    args = argparse.Namespace(
        lookback_days=5,
        effort=None,
        root="/default",
        mail_root="/default-mail",
        ollama=False,
        no_ollama=True,
        no_grouping=False,
        grouping=True,
        grouping_min_size=3,
        folder=None,
        exclude_folder=None,
        output=[],
        ollama_model=None,
        summary_profile=None,
    )
    # CLI argv includes the emitted flags → sticky must not override.
    apply_sticky_to_namespace(args, sticky, argv=["digest", *emitted])
    assert args.lookback_days == 5
    assert args.effort is None

    # Without CLI flags, sticky applies.
    apply_sticky_to_namespace(args, sticky, argv=["digest"])
    assert args.lookback_days == 9
    assert args.effort == "light"
    assert args.ollama is True
    assert args.no_ollama is False
    assert args.no_grouping is True
    assert args.grouping is False
    assert args.folder == ["tech"]
    assert args.output == ["json", "txt"]
    assert args.summary_profile == "brief"
