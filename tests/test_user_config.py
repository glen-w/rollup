"""Tests for optional TOML user configuration."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from rollup.user_config import (
    UserConfigError,
    apply_sticky_to_namespace,
    extract_config_path,
    load_user_config,
    parse_toml_dict,
)


def test_parse_rejects_unknown_top_level(tmp_path: Path) -> None:
    with pytest.raises(UserConfigError, match="unknown top-level"):
        parse_toml_dict({"nope": 1}, path=tmp_path / "x.toml")


def test_parse_folder_themes_and_sticky(tmp_path: Path) -> None:
    cfg = parse_toml_dict(
        {
            "lookback_days": 3,
            "folder": ["tech", "hoops"],
            "folders": {
                "tech": {"emoji": "💻", "accent": "#4a7fd4"},
            },
            "profiles": {
                "fast": {"lookback_days": 1, "effort": "light"},
            },
        },
        path=tmp_path / "x.toml",
    )
    assert cfg.values["lookback_days"] == 3
    assert cfg.values["folder"] == ["tech", "hoops"]
    assert cfg.folder_themes["tech"].emoji == "💻"
    assert cfg.profiles["fast"]["effort"] == "light"


def test_load_merge_later_wins(tmp_path: Path) -> None:
    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    a.write_text('lookback_days = 3\neffort = "light"\n', encoding="utf-8")
    b.write_text('lookback_days = 1\n', encoding="utf-8")
    loaded = load_user_config(search_paths=(a, b))
    assert loaded.values["lookback_days"] == 1
    assert loaded.values["effort"] == "light"
    assert loaded.sources == (a, b)


def test_explicit_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(UserConfigError, match="not found"):
        load_user_config(explicit_path=tmp_path / "missing.toml")


def test_extract_config_path() -> None:
    path, rest = extract_config_path(
        ["--config", "/tmp/x.toml", "digest", "--lookback-days", "3"]
    )
    assert path == "/tmp/x.toml"
    assert rest == ["digest", "--lookback-days", "3"]


def test_apply_sticky_respects_cli_flags() -> None:
    args = argparse.Namespace(
        lookback_days=5,  # already set by argparse from --lookback-days 5
        effort=None,
        folder=None,
        root="/default/root",
        output=[],
    )
    apply_sticky_to_namespace(
        args,
        {"lookback_days": 1, "effort": "light", "folder": ["tech"], "root": "/from/toml"},
        argv=["digest", "--lookback-days", "5"],
    )
    assert args.lookback_days == 5
    assert args.effort == "light"
    assert args.folder == ["tech"]
    assert args.root == "/from/toml"


def test_apply_sticky_output_writers() -> None:
    args = argparse.Namespace(output=[])
    apply_sticky_to_namespace(
        args,
        {"output": ["json", "txt"]},
        argv=["digest"],
    )
    assert args.output == ["json", "txt"]

    args2 = argparse.Namespace(output=[])
    apply_sticky_to_namespace(
        args2,
        {"output": ["all"]},
        argv=["digest"],
    )
    assert args2.output == []

    args3 = argparse.Namespace(output=["xteink"])
    apply_sticky_to_namespace(
        args3,
        {"output": ["json"]},
        argv=["digest", "--output", "xteink"],
    )
    assert args3.output == ["xteink"]


def test_parse_output_sticky(tmp_path: Path) -> None:
    cfg = parse_toml_dict({"output": "none"}, path=tmp_path / "x.toml")
    assert cfg.values["output"] == ["none"]
    cfg2 = parse_toml_dict(
        {"output": ["xteink", "JSON"]}, path=tmp_path / "y.toml"
    )
    assert cfg2.values["output"] == ["xteink", "json"]


def test_sticky_validation_errors(tmp_path: Path) -> None:
    with pytest.raises(UserConfigError, match="lookback_days"):
        parse_toml_dict({"lookback_days": 0}, path=tmp_path / "x.toml")
    with pytest.raises(UserConfigError, match="ollama"):
        parse_toml_dict({"ollama": "yes"}, path=tmp_path / "x.toml")
    with pytest.raises(UserConfigError, match="output"):
        parse_toml_dict({"output": []}, path=tmp_path / "x.toml")
    with pytest.raises(UserConfigError, match="unknown"):
        parse_toml_dict(
            {"folders": {"tech": {"emoji": "x", "nope": 1}}},
            path=tmp_path / "x.toml",
        )


def test_apply_sticky_ollama_and_grouping() -> None:
    from rollup.user_config import flag_present

    assert flag_present(["digest", "--effort=light"], "--effort")
    assert not flag_present(["digest", "--lookback-days", "3"], "--effort")

    args = argparse.Namespace(
        ollama=None, no_ollama=None, no_grouping=None, grouping=None
    )
    apply_sticky_to_namespace(
        args,
        {"ollama": True, "no_grouping": True},
        argv=["digest"],
    )
    assert args.ollama is True
    assert args.no_ollama is False
    assert args.no_grouping is True

    args2 = argparse.Namespace(
        ollama=False, no_ollama=True, no_grouping=False, grouping=None
    )
    apply_sticky_to_namespace(
        args2,
        {"ollama": True, "no_grouping": True},
        argv=["digest", "--no-ollama"],
    )
    assert args2.ollama is False
    assert args2.no_ollama is True
    assert args2.no_grouping is True  # grouping flags absent → sticky applies


def test_extract_config_equals_form() -> None:
    path, rest = extract_config_path(
        ["--config=/tmp/x.toml", "digest", "--lookback-days", "3"]
    )
    assert path == "/tmp/x.toml"
    assert rest == ["digest", "--lookback-days", "3"]


def test_profile_merge_across_files(tmp_path: Path) -> None:
    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    a.write_text(
        '[profiles.fast]\nlookback_days = 3\neffort = "light"\n',
        encoding="utf-8",
    )
    b.write_text(
        '[profiles.fast]\nlookback_days = 1\n',
        encoding="utf-8",
    )
    loaded = load_user_config(search_paths=(a, b))
    assert loaded.profiles["fast"]["lookback_days"] == 1
    assert loaded.profiles["fast"]["effort"] == "light"


def test_empty_search_paths_and_invalid_toml(tmp_path: Path) -> None:
    loaded = load_user_config(search_paths=())
    assert loaded.values == {}
    assert loaded.sources == ()
    bad = tmp_path / "bad.toml"
    bad.write_text("[[[not valid", encoding="utf-8")
    with pytest.raises(UserConfigError):
        load_user_config(search_paths=(bad,))


def test_parse_effort_model_overrides(tmp_path: Path) -> None:
    cfg = parse_toml_dict(
        {
            "efforts": {
                "high": {
                    "rough": "custom-rough:latest",
                    "ollama_model": "custom-group:latest",
                }
            }
        },
        path=tmp_path / "x.toml",
    )
    assert cfg.efforts["high"].profiles["rough"] == "custom-rough:latest"
    assert cfg.efforts["high"].ollama_model == "custom-group:latest"
    assert cfg.efforts["high"].final_review_model is None


def test_parse_rejects_unknown_effort_name(tmp_path: Path) -> None:
    with pytest.raises(UserConfigError, match="unknown effort"):
        parse_toml_dict(
            {"efforts": {"turbo": {"rough": "x"}}},
            path=tmp_path / "x.toml",
        )


def test_parse_llm_provider_and_model(tmp_path: Path) -> None:
    cfg = parse_toml_dict(
        {"llm_provider": "LiteLLM", "llm_model": "openai/gpt-4o"},
        path=tmp_path / "x.toml",
    )
    assert cfg.values["llm_provider"] == "litellm"
    assert cfg.values["llm_model"] == "openai/gpt-4o"


def test_parse_scholar_table(tmp_path: Path) -> None:
    cfg = parse_toml_dict(
        {
            "scholar": {
                "mode": "detailed",
                "max_papers_per_email": 4,
                "max_fetches_per_run": 12,
            }
        },
        path=tmp_path / "x.toml",
    )
    assert cfg.scholar.mode == "detailed"
    assert cfg.scholar.max_papers_per_email == 4
    assert cfg.scholar.max_fetches_per_run == 12


def test_parse_rejects_bad_scholar_mode(tmp_path: Path) -> None:
    with pytest.raises(UserConfigError, match=r"\[scholar\]\.mode"):
        parse_toml_dict({"scholar": {"mode": "fetch-all"}}, path=tmp_path / "x.toml")


def test_parse_rejects_bad_llm_provider(tmp_path: Path) -> None:
    with pytest.raises(UserConfigError, match="llm_provider"):
        parse_toml_dict({"llm_provider": "openai"}, path=tmp_path / "x.toml")


def test_parse_ui_rejects_invalid_landing(tmp_path: Path) -> None:
    with pytest.raises(UserConfigError, match="landing_page"):
        parse_toml_dict(
            {"ui": {"landing_page": "dashboard"}},
            path=tmp_path / "x.toml",
        )
    with pytest.raises(UserConfigError, match="preferred_view"):
        parse_toml_dict(
            {"ui": {"preferred_view": "pdf"}},
            path=tmp_path / "x.toml",
        )


def test_parse_rejects_empty_effort_slot(tmp_path: Path) -> None:
    with pytest.raises(UserConfigError, match="must be a non-empty string"):
        parse_toml_dict(
            {"efforts": {"high": {"max": "   "}}},
            path=tmp_path / "x.toml",
        )
    with pytest.raises(UserConfigError, match="unknown key"):
        parse_toml_dict(
            {"efforts": {"high": {"turbo": "x"}}},
            path=tmp_path / "x.toml",
        )


def test_merge_effort_overrides_across_files(tmp_path: Path) -> None:
    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    a.write_text(
        '[efforts.high]\nrough = "base-rough:1"\nollama_model = "base-group:1"\n',
        encoding="utf-8",
    )
    b.write_text(
        '[efforts.high]\nmax = "overlay-max:1"\nfinal_review_model = "overlay-review:1"\n',
        encoding="utf-8",
    )
    loaded = load_user_config(search_paths=(a, b))
    high = loaded.efforts["high"]
    assert high.profiles["rough"] == "base-rough:1"
    assert high.profiles["max"] == "overlay-max:1"
    assert high.ollama_model == "base-group:1"
    assert high.final_review_model == "overlay-review:1"
