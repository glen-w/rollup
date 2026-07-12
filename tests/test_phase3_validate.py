"""Central Phase-3 runtime validation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from rollup.config import Config, DEFAULT_FINAL_REVIEW_MAX_CHANGED_CHARS_RATIO
from rollup.final_review_profiles import FinalReviewConfigError
from rollup.phase3_validate import validate_phase3_runtime_config
from rollup.run_options import GroupingConfig, RunOptions


def _config(**kwargs) -> Config:
    base = dict(
        root=Path("/tmp"),
        mail_root=Path("/tmp/mail"),
        output_dir=Path("/tmp/out"),
        state_dir=Path("/tmp/state"),
        log_dir=Path("/tmp/logs"),
        lookback_days=7,
        folders_include=(),
        folders_exclude=(),
        no_ollama=True,
        include_seen_undated=False,
        rebuild_summaries=False,
        max_body_chars=1000,
        max_chars_for_llm=1000,
        max_display_links=8,
        ollama_url="http://127.0.0.1:11434/api/generate",
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
        final_review_max_changed_chars_ratio=DEFAULT_FINAL_REVIEW_MAX_CHANGED_CHARS_RATIO,
    )
    base.update(kwargs)
    return Config(**base)


def test_group_summaries_requires_ollama() -> None:
    with pytest.raises(FinalReviewConfigError, match="requires Ollama"):
        validate_phase3_runtime_config(
            _config(group_summaries_enabled=True, no_ollama=True),
            grouping=GroupingConfig(enabled=True),
        )


def test_group_summaries_requires_grouping() -> None:
    with pytest.raises(FinalReviewConfigError, match="requires grouping"):
        validate_phase3_runtime_config(
            _config(group_summaries_enabled=True, no_ollama=False),
            grouping=GroupingConfig(enabled=False),
        )


def test_non_primary_variant_rejected() -> None:
    with pytest.raises(FinalReviewConfigError, match="primary"):
        validate_phase3_runtime_config(
            _config(group_summary_variant_policy="each"),
        )


def test_removed_group_summary_profile_rejected() -> None:
    cfg = _config()
    # Simulate stale config object still carrying the removed knob.
    proxy = SimpleNamespace(**{**cfg.__dict__, "group_summary_profile": "x"})
    with pytest.raises(FinalReviewConfigError, match="removed"):
        validate_phase3_runtime_config(proxy)  # type: ignore[arg-type]


def test_cron_apply_requires_allow() -> None:
    with pytest.raises(FinalReviewConfigError, match="allow-cron-apply"):
        validate_phase3_runtime_config(
            _config(
                final_review_enabled=True,
                final_review_mode="apply",
                final_review_allow_cron_apply=False,
                no_ollama=False,
            ),
            run_options=RunOptions(cron=True),
        )


def test_cron_apply_non_conservative_rejected() -> None:
    with pytest.raises(FinalReviewConfigError, match="conservative"):
        validate_phase3_runtime_config(
            _config(
                final_review_enabled=True,
                final_review_mode="apply",
                final_review_allow_cron_apply=True,
                final_review_apply_policy="standard",
                no_ollama=False,
            ),
            run_options=RunOptions(cron=True),
        )


def test_resolve_apply_policy_unattended_for_conservative() -> None:
    validated = validate_phase3_runtime_config(
        _config(
            final_review_enabled=True,
            final_review_mode="apply",
            final_review_apply_policy="conservative",
            no_ollama=False,
        ),
        run_options=RunOptions(cron=False),
    )
    assert validated.apply_policy is not None
    assert validated.apply_policy.unattended is True
