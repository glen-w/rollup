"""Tests for machine-power effort presets."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from rollup.cli import (
    _build_config,
    _effort_profile_set_conflict_error,
    build_parser,
    cmd_digest,
)
from rollup.config import (
    DEFAULT_EFFORT,
    DEFAULT_MAX_CHARS_FOR_LLM,
    DEFAULT_OLLAMA_MODEL,
)
from rollup.effort import (
    DEFAULT_FINAL_REVIEW_MODEL,
    UnknownEffortError,
    get_effort_preset,
    list_effort_presets,
    resolve_effort_name,
    resolve_profile_set,
)
from rollup.summary_profiles import get_builtin_summary_profile_set


def test_resolve_effort_name_defaults_to_balanced() -> None:
    assert resolve_effort_name(None) == DEFAULT_EFFORT == "balanced"
    assert resolve_effort_name("high") == "high"


def test_unknown_effort_raises() -> None:
    with pytest.raises(UnknownEffortError):
        get_effort_preset("turbo")
    with pytest.raises(UnknownEffortError):
        resolve_effort_name("turbo")


def test_balanced_matches_builtin_ladder() -> None:
    builtin = get_builtin_summary_profile_set()
    balanced = get_effort_preset("balanced")
    assert balanced.profile_set.type_routes == builtin.type_routes
    assert balanced.profile_set.default_profile == builtin.default_profile
    assert set(balanced.profile_set.profiles) == set(builtin.profiles)
    for name, profile in builtin.profiles.items():
        effort_profile = balanced.profile_set.profiles[name]
        assert effort_profile.model == profile.model
        assert effort_profile.num_ctx == profile.num_ctx
        assert effort_profile.timeout_seconds == profile.timeout_seconds
        assert effort_profile.num_predict == profile.num_predict
        assert effort_profile.think == profile.think
    assert balanced.ollama_model == DEFAULT_OLLAMA_MODEL
    assert balanced.final_review_model == DEFAULT_FINAL_REVIEW_MODEL
    assert balanced.max_chars_for_llm == DEFAULT_MAX_CHARS_FOR_LLM


def test_all_efforts_share_type_routes() -> None:
    routes = get_effort_preset("balanced").profile_set.type_routes
    for preset in list_effort_presets():
        assert preset.profile_set.type_routes == routes
        assert set(preset.profile_set.profiles) == {"rough", "standard", "deep", "max"}


def test_light_and_high_model_ladder() -> None:
    light = get_effort_preset("light")
    assert light.profile_set.profiles["rough"].model == "llama3.2:3b"
    assert light.profile_set.profiles["standard"].model == "llama3.2:3b"
    assert light.profile_set.profiles["deep"].model == "qwen2.5:7b"
    assert light.profile_set.profiles["max"].model == "qwen2.5:7b"
    assert light.max_chars_for_llm == 20_000

    high = get_effort_preset("high")
    assert high.profile_set.profiles["rough"].model == "qwen2.5:7b"
    assert high.profile_set.profiles["standard"].model == "gpt-oss:20b"
    assert high.profile_set.profiles["standard"].think == "low"
    assert high.profile_set.profiles["standard"].num_predict == 2048
    assert high.profile_set.profiles["deep"].model == "qwen3.6:27b"
    assert high.profile_set.profiles["max"].model == "qwen3.6:27b"
    assert high.ollama_model == "qwen2.5:7b"
    assert high.final_review_model == "gpt-oss:20b"
    assert high.max_chars_for_llm == 50_000
    assert high.profile_set.profiles["standard"].num_ctx == 32768
    assert high.profile_set.profiles["deep"].timeout_seconds == 600


def test_resolve_profile_set_uses_effort() -> None:
    high = resolve_profile_set(effort="high")
    assert high.name == "high"
    assert high.profiles["standard"].model == "gpt-oss:20b"


def test_resolve_profile_set_custom_json(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    path.write_text(
        """
        {
          "schema_version": 1,
          "name": "custom",
          "default_profile": "standard",
          "profiles": {
            "standard": {
              "provider": "ollama",
              "model": "custom:7b",
              "temperature": 0.2,
              "prompt_style": "standard"
            }
          },
          "type_routes": {}
        }
        """,
        encoding="utf-8",
    )
    loaded = resolve_profile_set(summary_profile_set_path=str(path))
    assert loaded.profiles["standard"].model == "custom:7b"


def test_effort_conflicts_with_summary_profile_set() -> None:
    args = argparse.Namespace(effort="high", summary_profile_set="x.json")
    err = _effort_profile_set_conflict_error(args)
    assert err is not None
    assert "--effort" in err
    assert "--summary-profile-set" in err

    ok = argparse.Namespace(effort=None, summary_profile_set="x.json")
    assert _effort_profile_set_conflict_error(ok) is None

    ok2 = argparse.Namespace(effort="high", summary_profile_set=None)
    assert _effort_profile_set_conflict_error(ok2) is None


def test_build_config_applies_high_effort_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["digest", "--effort", "high", "--root", "/tmp/root", "--mail-root", "/tmp/mail"]
    )
    config = _build_config(args)
    assert config.effort == "high"
    assert config.ollama_model == "qwen2.5:7b"
    assert config.final_review_model == "gpt-oss:20b"
    assert config.max_chars_for_llm == 50_000


def test_build_config_expands_user_in_paths() -> None:
    """Sticky/TOML paths like ~/Documents/... must not stay cwd-relative."""
    parser = build_parser()
    args = parser.parse_args(
        [
            "digest",
            "--root",
            "~/mail/Newsletters.sbd",
            "--mail-root",
            "~/mail",
            "--output-dir",
            "~/Documents/rollup-outputs",
            "--state-dir",
            "~/rollup-state",
            "--log-dir",
            "~/rollup-logs",
        ]
    )
    config = _build_config(args)
    home = Path.home()
    assert config.root == home / "mail" / "Newsletters.sbd"
    assert config.mail_root == home / "mail"
    assert config.output_dir == home / "Documents" / "rollup-outputs"
    assert config.state_dir == home / "rollup-state"
    assert config.log_dir == home / "rollup-logs"
    assert "~" not in str(config.output_dir)


def test_build_config_explicit_flags_override_effort() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "digest",
            "--effort",
            "high",
            "--ollama-model",
            "my-custom:9b",
            "--final-review-model",
            "review:13b",
            "--max-chars-for-llm",
            "12345",
            "--root",
            "/tmp/root",
            "--mail-root",
            "/tmp/mail",
        ]
    )
    config = _build_config(args)
    assert config.effort == "high"
    assert config.ollama_model == "my-custom:9b"
    assert config.final_review_model == "review:13b"
    assert config.max_chars_for_llm == 12345


def test_build_config_omitted_effort_matches_balanced() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["digest", "--root", "/tmp/root", "--mail-root", "/tmp/mail"]
    )
    config = _build_config(args)
    assert config.effort is None
    assert config.ollama_model == DEFAULT_OLLAMA_MODEL
    assert config.final_review_model == DEFAULT_FINAL_REVIEW_MODEL
    assert config.max_chars_for_llm == DEFAULT_MAX_CHARS_FOR_LLM


def test_cmd_digest_rejects_effort_with_profile_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "digest",
            "--effort",
            "high",
            "--summary-profile-set",
            str(tmp_path / "x.json"),
        ]
    )
    code = cmd_digest(args)
    captured = capsys.readouterr()
    assert code == 1
    assert "Cannot combine --effort" in captured.err


def test_cmd_digest_list_efforts(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    args = parser.parse_args(["digest", "--list-efforts"])
    code = cmd_digest(args)
    captured = capsys.readouterr()
    assert code == 0
    assert "light:" in captured.out
    assert "balanced:" in captured.out
    assert "high:" in captured.out
