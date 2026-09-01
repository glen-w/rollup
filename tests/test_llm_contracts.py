"""Schema v11 summaries_litellm migration and LiteLLM contract tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rollup.config import Config, DEFAULT_FINAL_REVIEW_MAX_CHANGED_CHARS_RATIO
from rollup.effective_run import resolve_effective_run
from rollup.llm_validate import LlmJobValidationError, validate_executable_llm_jobs
from rollup.provider_options import ProviderOptionsError, reject_litellm_ollama_model
from rollup.run_options import GroupingConfig, RunOptions
from rollup.state import (
    SCHEMA_VERSION,
    ensure_summaries_litellm_v11,
    get_schema_version,
    init_db,
)
from rollup.summary_profiles import (
    SummaryProfile,
    SummaryProfileSet,
    validate_summary_profile_set,
)


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
        ollama_url="http://localhost:11434/api/generate",
        ollama_model="llama3.2:3b",
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


def test_schema_v11_fresh_has_summaries_litellm(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    assert get_schema_version(conn) == SCHEMA_VERSION == 12
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(rollup_runs)").fetchall()
    }
    assert "summaries_litellm" in cols
    conn.close()


def test_schema_v11_idempotent(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    ensure_summaries_litellm_v11(conn)
    ensure_summaries_litellm_v11(conn)
    assert get_schema_version(conn) == 12
    conn.close()


def test_no_ollama_suppresses_final_review_network() -> None:
    eff = resolve_effective_run(
        _config(no_ollama=True, final_review_enabled=True),
        RunOptions(dry_run=False),
        GroupingConfig(enabled=True),
    )
    assert eff.allow_summary_network is False
    assert eff.allow_final_review_network is False
    assert eff.allow_group_summary_network is False


def test_dry_run_suppresses_all_llm_network() -> None:
    eff = resolve_effective_run(
        _config(no_ollama=False, final_review_enabled=True, group_summaries_enabled=True),
        RunOptions(dry_run=True),
        GroupingConfig(enabled=True),
    )
    assert eff.allow_summary_network is False
    assert eff.allow_final_review_network is False
    assert eff.allow_group_summary_network is False


def test_reject_ollama_via_litellm_model() -> None:
    with pytest.raises(ProviderOptionsError, match="routes Ollama"):
        reject_litellm_ollama_model("ollama/llama3.2", context="test")


def test_litellm_profile_rejects_think() -> None:
    profiles = {
        "cloud": SummaryProfile(
            name="cloud",
            provider="litellm",
            model="openai/gpt-4o",
            temperature=0.2,
            think=True,
        )
    }
    profile_set = SummaryProfileSet(
        profiles=profiles,
        default_profile="cloud",
        type_routes={},
    )
    issues = validate_summary_profile_set(profile_set)
    assert any(i.code == "litellm_incompatible_option" for i in issues)


def test_validate_litellm_jobs_requires_extra(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "litellm" or name.startswith("litellm."):
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    cfg = _config(
        no_ollama=False,
        llm_provider="litellm",
        llm_model="openai/gpt-4o",
        group_summaries_enabled=True,
    )
    with pytest.raises((LlmJobValidationError, Exception)):
        validate_executable_llm_jobs(cfg, None)


def test_no_ollama_does_not_import_litellm(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__
    imported: list[str] = []

    def tracking_import(name, *args, **kwargs):
        imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracking_import)
    cfg = _config(no_ollama=True, llm_provider="litellm", llm_model="openai/gpt-4o")
    validate_executable_llm_jobs(cfg, None)
    assert not any(n == "litellm" or n.startswith("litellm.") for n in imported)


def test_profile_provider_not_rewritten_by_global_llm_provider() -> None:
    from rollup.summary_plan import SummaryJob
    from rollup.llm_validate import validate_summary_job

    cfg = _config(
        no_ollama=False,
        llm_provider="litellm",
        llm_model="openai/gpt-4o",
    )
    job = SummaryJob(
        message_key="k",
        content_hash="h",
        canonical_newsletter_type="news",
        summary_input_hash="i",
        profile_name="local",
        prompt_style="rough",
        provider="ollama",
        model="llama3.2:3b",
        options={},
        think=False,
        temperature=0.2,
        num_ctx=None,
        timeout_seconds=None,
        variant_name="default",
    )
    resolved = validate_summary_job(job, cfg)
    assert resolved.provider == "ollama"
    assert resolved.model == "llama3.2:3b"
