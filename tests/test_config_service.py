"""Tests for shared configuration service."""

from __future__ import annotations

from pathlib import Path

import pytest

from rollup.config_service import (
    ConfigConflictError,
    ConfigPatch,
    ConfigValidationError,
    apply_and_save,
    build_digest_argv,
    compute_revision,
    effective_diff,
    load_document,
    patch_from_form_values,
    resolve_config_path,
    resolve_effective,
    validate_patch,
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


def test_effort_overrides_roundtrip(tmp_path: Path) -> None:
    from rollup.effort import EffortModelOverride

    path = tmp_path / "config.toml"
    path.write_text("lookback_days = 7\n", encoding="utf-8")
    rev = compute_revision(path)
    patch = ConfigPatch(
        effort_overrides={
            "high": EffortModelOverride(
                profiles={"max": "my-max:33b"},
                ollama_model="my-group:7b",
            )
        }
    )
    saved = apply_and_save(path, patch, expected_revision=rev)
    assert saved.loaded.efforts["high"].profiles["max"] == "my-max:33b"
    assert saved.loaded.efforts["high"].ollama_model == "my-group:7b"
    text = path.read_text(encoding="utf-8")
    assert "[efforts.high]" in text or 'max = "my-max:33b"' in text
    eff = resolve_effective(saved.loaded)
    assert "efforts.high.max" in {row[0] for row in []} or (
        eff.effort_overrides["high"].profiles["max"] == "my-max:33b"
    )


def test_resolve_config_path_falls_back_to_xdg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert resolve_config_path() == (
        Path.home() / ".config" / "rollup" / "config.toml"
    ).resolve()


def test_load_document_missing_and_search_conflict(tmp_path: Path) -> None:
    missing = tmp_path / "absent.toml"
    doc = load_document(explicit=missing)
    assert doc.exists is False
    assert doc.revision == "missing"
    assert doc.search_conflict is False

    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    a.write_text("lookback_days = 3\n", encoding="utf-8")
    b.write_text("lookback_days = 1\n", encoding="utf-8")
    conflicted = load_document(search_paths=(a, b))
    assert conflicted.search_conflict is True
    assert set(conflicted.conflicting_paths) == {a.resolve(), b.resolve()}
    assert conflicted.loaded.values["lookback_days"] == 1


def test_resolve_effective_unknown_profile_falls_back() -> None:
    from rollup.run_profiles import DEFAULT_RUN_PROFILE
    from rollup.user_config import LoadedUserConfig

    loaded = LoadedUserConfig(values={"profile": "does-not-exist", "lookback_days": 9})
    eff = resolve_effective(loaded)
    assert eff.profile_name == DEFAULT_RUN_PROFILE
    assert "lookback_days" in eff.sticky


def test_validate_paths_requires_mail_root(tmp_path: Path) -> None:
    from rollup.config_service import validate_paths_for_sticky

    issues = validate_paths_for_sticky({"root": str(tmp_path / "Newsletters.sbd")})
    assert any(i.field == "mail_root" for i in issues)


def test_validate_patch_rejects_bad_lookback() -> None:
    from rollup.user_config import LoadedUserConfig

    issues = validate_patch(
        patch_from_form_values(lookback_days=0),
        base=LoadedUserConfig(),
    )
    assert any(i.severity == "error" for i in issues)


def test_apply_and_save_validation_error(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("lookback_days = 7\n", encoding="utf-8")
    with pytest.raises(ConfigValidationError):
        apply_and_save(
            path,
            patch_from_form_values(lookback_days=0),
            expected_revision=compute_revision(path),
        )


def test_apply_and_save_creates_new_file(tmp_path: Path) -> None:
    path = tmp_path / "new.toml"
    saved = apply_and_save(
        path,
        patch_from_form_values(lookback_days=4, effort="light"),
        expected_revision="missing",
    )
    assert path.is_file()
    assert saved.exists is True
    assert saved.loaded.values["lookback_days"] == 4
    assert saved.loaded.values["effort"] == "light"


def test_apply_and_save_folder_themes_and_remove_profile(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "lookback_days = 7\n\n[profiles.old]\nlookback_days = 2\n",
        encoding="utf-8",
    )
    patch = ConfigPatch(
        folder_themes={
            "tech": FolderThemeOverride(
                emoji="💻",
                accent="#abc123",
                display_name="Tech",
                order=1,
            )
        },
        remove_profiles={"old"},
    )
    saved = apply_and_save(path, patch, expected_revision=compute_revision(path))
    theme = saved.loaded.folder_themes["tech"]
    assert theme.emoji == "💻"
    assert theme.accent == "#abc123"
    assert theme.display_name == "Tech"
    assert theme.order == 1
    assert "old" not in saved.loaded.profiles


def test_effective_diff_includes_effort_overrides() -> None:
    from rollup.effort import EffortModelOverride
    from rollup.user_config import LoadedUserConfig

    before = resolve_effective(
        LoadedUserConfig(values={"lookback_days": 7}, profiles={"solo": {}}),
        profile_name="solo",
    )
    after = resolve_effective(
        LoadedUserConfig(
            values={"lookback_days": 3, "ollama": True},
            profiles={"solo": {}},
            efforts={
                "high": EffortModelOverride(
                    profiles={"max": "my-max:33b"},
                    final_review_model="review:1",
                )
            },
        ),
        profile_name="solo",
    )
    rows = {key: (old, new) for key, old, new in effective_diff(before, after)}
    assert rows["lookback_days"] == ("7", "3")
    assert rows["ollama_contacted"] == ("no", "yes")
    assert rows["efforts.high.max"][1] == "my-max:33b"
    assert rows["efforts.high.final_review_model"][1] == "review:1"


def test_patch_from_form_clears_empty_strings() -> None:
    patch = patch_from_form_values(
        ollama_model="",
        llm_model="",
        folder=[],
        exclude_folder=[],
        output=[],
        grouping_min_size=3,
        llm_provider="litellm",
    )
    assert "ollama_model" in patch.clear_values
    assert "llm_model" in patch.clear_values
    assert "folder" in patch.clear_values
    assert "exclude_folder" in patch.clear_values
    assert patch.values["output"] == ["all"]
    assert patch.values["llm_provider"] == "litellm"
    assert patch.values["grouping_min_size"] == 3


def test_build_digest_argv_extra_and_config_path(tmp_path: Path) -> None:
    from rollup.user_config import LoadedUserConfig

    loaded = LoadedUserConfig(
        values={"lookback_days": 1, "ollama": False, "root": str(tmp_path)}
    )
    eff = resolve_effective(loaded, profile_name="daily")
    argv = build_digest_argv(
        eff,
        config_path=tmp_path / "rollup.toml",
        extra=["--single-model", "solo:7b"],
    )
    assert argv[:3] == ["--config", str(tmp_path / "rollup.toml"), "digest"]
    assert argv[-2:] == ["--single-model", "solo:7b"]
    assert "--dry-run" not in argv
