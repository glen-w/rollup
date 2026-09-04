"""EffectiveRun resolution contracts."""

from __future__ import annotations

from pathlib import Path

from rollup.config import Config
from rollup.effective_run import EffectiveRun, resolve_effective_run
from rollup.run_options import GroupingConfig, RunOptions


def _config(**overrides) -> Config:
    base = dict(
        root=Path("/tmp/root"),
        mail_root=Path("/tmp/mail"),
        output_dir=Path("/tmp/output"),
        state_dir=Path("/tmp/state"),
        log_dir=Path("/tmp/logs"),
        lookback_days=7,
        folders_include=("tech",),
        folders_exclude=("old",),
        no_ollama=True,
        include_seen_undated=False,
        rebuild_summaries=False,
        max_body_chars=1000,
        max_chars_for_llm=2000,
        max_display_links=8,
        ollama_url="http://localhost:11434/api/generate",
        ollama_model="m",
        allow_remote_ollama=False,
        summary_profile=None,
        summary_variants=(),
        summary_type_routing=None,
        summary_profile_set_path=None,
        export_summary_profile_set_path=None,
        list_summary_profiles=False,
        list_newsletter_types=False,
        summary_routing_report=False,
    )
    base.update(overrides)
    return Config(**base)


def test_effective_run_fields_are_flat() -> None:
    effective = resolve_effective_run(
        _config(final_review_enabled=True, final_review_mode="report"),
        RunOptions(dry_run=True, verbose=True, write_manifest=False),
        grouping=GroupingConfig(enabled=True, min_group_size=4, report=True),
    )

    assert isinstance(effective, EffectiveRun)
    assert not hasattr(effective, "config")
    assert not hasattr(effective, "run_options")
    assert effective.root == Path("/tmp/root")
    assert effective.dry_run is True
    assert effective.verbose is True
    assert effective.write_manifest is False
    assert effective.grouping_enabled is True
    assert effective.grouping_min_group_size == 4
    assert effective.db_path == Path("/tmp/state/rollup.db")


def test_effective_run_network_arms_disabled_by_dry_run() -> None:
    effective = resolve_effective_run(
        _config(
            no_ollama=False,
            final_review_enabled=True,
            group_summaries_enabled=True,
            no_linkedin=False,
            no_reddit=False,
            no_webpage=False,
        ),
        RunOptions(dry_run=True),
        grouping=GroupingConfig(enabled=True),
    )

    assert effective.allow_summary_network is False
    assert effective.allow_final_review_network is False
    assert effective.allow_group_summary_network is False
    assert effective.allow_linkedin_network is False
    assert effective.allow_reddit_network is False
    assert effective.allow_webpage_network is False
    assert effective.allow_scholar_network is False


def test_effective_run_network_arms_enabled_by_stage() -> None:
    effective = resolve_effective_run(
        _config(
            no_ollama=False,
            final_review_enabled=True,
            group_summaries_enabled=True,
            no_linkedin=False,
            no_reddit=False,
            no_webpage=False,
        ),
        RunOptions(dry_run=False),
        grouping=GroupingConfig(enabled=True),
    )

    assert effective.allow_summary_network is True
    assert effective.allow_final_review_network is True
    assert effective.allow_group_summary_network is True
    assert effective.allow_linkedin_network is True
    assert effective.allow_reddit_network is True
    assert effective.allow_webpage_network is True
    assert effective.allow_scholar_network is False


def test_effective_run_network_arms_respect_disabled_stages() -> None:
    effective = resolve_effective_run(
        _config(
            no_ollama=True,
            final_review_enabled=False,
            group_summaries_enabled=False,
            no_linkedin=True,
            no_reddit=True,
            no_webpage=True,
        ),
        RunOptions(dry_run=False),
        grouping=GroupingConfig(enabled=False),
    )

    assert effective.allow_summary_network is False
    assert effective.allow_final_review_network is False
    assert effective.allow_group_summary_network is False
    assert effective.allow_linkedin_network is False
    assert effective.allow_reddit_network is False
    assert effective.allow_webpage_network is False
    assert effective.allow_scholar_network is False
    assert effective.apply_policy is None


def test_final_review_network_independent_of_no_ollama() -> None:
    """--final-review may call a model even when LLM summaries are off."""
    effective = resolve_effective_run(
        _config(no_ollama=True, final_review_enabled=True),
        RunOptions(dry_run=False),
        grouping=GroupingConfig(enabled=True),
    )
    assert effective.allow_summary_network is False
    assert effective.allow_final_review_network is True
    assert effective.allow_group_summary_network is False


def test_webpage_network_on_by_default_when_not_dry_run() -> None:
    effective = resolve_effective_run(
        _config(),
        RunOptions(dry_run=False),
    )
    assert effective.allow_webpage_network is True
    assert effective.allow_linkedin_network is False
    assert effective.allow_reddit_network is False
    assert effective.allow_scholar_network is False


def test_toml_enables_linkedin_and_reddit_without_cli_flags(tmp_path: Path) -> None:
    from rollup.cli import _apply_loaded_config, _build_config
    from rollup.cli_parser import build_parser
    from rollup.user_config import load_user_config

    cfg = tmp_path / "rollup.toml"
    cfg.write_text(
        "[linkedin]\nenabled = true\n[reddit]\nenabled = true\n",
        encoding="utf-8",
    )
    loaded = load_user_config(explicit_path=cfg)
    parser = build_parser()
    raw = ["digest"]
    args = parser.parse_args(raw)
    _apply_loaded_config(args, loaded, raw)
    assert args.linkedin is True
    assert args.reddit is True
    config = _build_config(args)
    assert config.linkedin_enabled is True
    assert config.reddit_enabled is True
    effective = resolve_effective_run(config, RunOptions(dry_run=False))
    assert effective.allow_linkedin_network is True
    assert effective.allow_reddit_network is True


def test_scholar_detailed_enables_network_when_not_dry_run() -> None:
    from rollup.scholar.config import ScholarConfig

    effective = resolve_effective_run(
        _config(scholar=ScholarConfig(mode="detailed")),
        RunOptions(dry_run=False),
    )
    assert effective.allow_scholar_network is True
    dry = resolve_effective_run(
        _config(scholar=ScholarConfig(mode="detailed")),
        RunOptions(dry_run=True),
    )
    assert dry.allow_scholar_network is False


def test_cli_scholar_mode_overrides_toml(tmp_path: Path) -> None:
    from rollup.cli import _apply_loaded_config, _build_config
    from rollup.cli_parser import build_parser
    from rollup.user_config import load_user_config

    cfg = tmp_path / "rollup.toml"
    cfg.write_text('[scholar]\nmode = "detailed"\n', encoding="utf-8")
    loaded = load_user_config(explicit_path=cfg)
    parser = build_parser()
    raw = ["digest", "--scholar-mode", "default"]
    args = parser.parse_args(raw)
    args._loaded_user_config = loaded
    _apply_loaded_config(args, loaded, raw)
    config = _build_config(args)
    assert config.scholar.mode == "default"
    effective = resolve_effective_run(config, RunOptions(dry_run=False))
    assert effective.allow_scholar_network is False


def test_toml_scholar_detailed_enables_network(tmp_path: Path) -> None:
    from rollup.cli import _apply_loaded_config, _build_config
    from rollup.cli_parser import build_parser
    from rollup.user_config import load_user_config

    cfg = tmp_path / "rollup.toml"
    cfg.write_text('[scholar]\nmode = "detailed"\n', encoding="utf-8")
    loaded = load_user_config(explicit_path=cfg)
    parser = build_parser()
    raw = ["digest"]
    args = parser.parse_args(raw)
    args._loaded_user_config = loaded
    _apply_loaded_config(args, loaded, raw)
    config = _build_config(args)
    assert config.scholar.mode == "detailed"
    effective = resolve_effective_run(config, RunOptions(dry_run=False))
    assert effective.allow_scholar_network is True


def test_cli_no_linkedin_overrides_toml_enabled(tmp_path: Path) -> None:
    from rollup.cli import _apply_loaded_config, _build_config
    from rollup.cli_parser import build_parser
    from rollup.user_config import load_user_config

    cfg = tmp_path / "rollup.toml"
    cfg.write_text("[linkedin]\nenabled = true\n", encoding="utf-8")
    loaded = load_user_config(explicit_path=cfg)
    parser = build_parser()
    raw = ["digest", "--no-linkedin"]
    args = parser.parse_args(raw)
    _apply_loaded_config(args, loaded, raw)
    config = _build_config(args)
    assert config.linkedin_enabled is False
    effective = resolve_effective_run(config, RunOptions(dry_run=False))
    assert effective.allow_linkedin_network is False


def test_final_review_cli_independent_of_no_ollama() -> None:
    from rollup.cli import _build_config
    from rollup.cli_parser import build_parser

    args = build_parser().parse_args(["digest", "--final-review", "--no-ollama"])
    config = _build_config(args)
    assert config.no_ollama is True
    assert config.final_review_enabled is True
    effective = resolve_effective_run(config, RunOptions(dry_run=False))
    assert effective.allow_final_review_network is True
    assert effective.allow_summary_network is False
