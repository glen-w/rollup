"""Tests for final-review apply transforms."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.config import Config, DEFAULT_FINAL_REVIEW_MAX_CHANGED_CHARS_RATIO
from rollup.final_review_apply import (
    apply_final_review_patches,
    should_globally_skip_apply,
)
from rollup.final_review_profiles import FinalReviewConfigError, validate_final_review_config
from rollup.models import (
    ClassifiedMessage,
    DigestEntry,
    DigestReport,
    DigestStats,
    FinalReviewIssue,
    FinalReviewPatch,
    FinalReviewResult,
    ParsedMessage,
)


def _entry(key: str, summary: str) -> DigestEntry:
    parsed = ParsedMessage(
        message_key=key,
        content_hash="hash",
        folder_name="tech",
        relative_folder_path="tech",
        subject="Subject",
        sender="Sender <s@example.com>",
        date_raw="",
        date_parsed=datetime(2026, 7, 1, tzinfo=timezone.utc),
        body_text="body",
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=3,
        preview="preview",
        parse_warnings=(),
    )
    return DigestEntry(
        classified=ClassifiedMessage(
            parsed=parsed,
            newsletter_type="short_update",
            classification_scores=(("short_update", 1.0),),
        ),
        summary=summary,
        summary_source="preview_fallback",
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
        dry_run=False,
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
        verbose=False,
        quiet=True,
        final_review_max_changed_chars_ratio=DEFAULT_FINAL_REVIEW_MAX_CHANGED_CHARS_RATIO,
    )
    base.update(kwargs)
    return Config(**base)


def _report(entry: DigestEntry) -> DigestReport:
    now = datetime.now(timezone.utc)
    return DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=now,
        window_end=now,
        dated_by_folder={"tech": (entry,)},
        undated=(),
        stats=DigestStats(
            folders_scanned=1,
            messages_parsed=1,
            dated_included=1,
            undated_needing_review=0,
            skipped_outside_window=0,
            skipped_seen_undated=0,
            deduped_messages=0,
            parse_errors=0,
            summaries_ollama=0,
            summaries_cache=0,
            summaries_fallback=1,
        ),
    )


def _result(*, patches=(), status="pass", safe=True, source="ollama") -> FinalReviewResult:
    return FinalReviewResult(
        overall_status=status,
        safe_to_publish=safe,
        issues=(
            FinalReviewIssue(
                severity="minor",
                type="style_drift",
                location="tech",
                entry_id="mid:1",
                description="tweak",
                suggested_fix=None,
                safe_auto_fix=True,
                issue_id="iss-1",
            ),
        ),
        patches=patches,
        review_source=source,
        profile_name="strict",
        model="m",
        prompt_version="final_review_v2_apply",
        generated_at=datetime.now(timezone.utc),
        digest_fingerprint="fp",
        review_input_hash="ih",
        echoed_digest_fingerprint="fp",
        review_mode="apply",
    )


def test_validate_apply_mode_allowed() -> None:
    validate_final_review_config(mode="apply", provider="ollama", profile_name="strict")


def test_apply_updates_summary_purely() -> None:
    original = (
        "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda "
        "mu nu xi omicron pi rho sigma tau."
    )
    entry = _entry("mid:1", original)
    report = _report(entry)
    replacement = original.replace("tau.", "tau.")
    # Force a tiny within-ratio edit: swap one word of equal-ish length carefully
    replacement = original.replace("sigma tau.", "sigma tau!")
    assert abs(len(replacement) - len(original)) / max(len(original), 40) <= 0.08
    result = _result(
        patches=(
            FinalReviewPatch(
                entry_id="mid:1",
                field="summary",
                replacement=replacement,
                rationale="clarity",
                issue_id="iss-1",
            ),
        )
    )
    new_report, stats = apply_final_review_patches(report, result, _config())
    assert stats.applied == 1, stats.reasons
    assert report.dated_by_folder["tech"][0].summary == original
    applied = new_report.dated_by_folder["tech"][0]
    assert applied.summary == replacement
    assert applied.summary_source == "final_review_applied"
    assert applied.summary_original == original


def test_global_skip_on_fail() -> None:
    result = _result(status="fail", safe=False, patches=())
    assert should_globally_skip_apply(result) is not None


def test_reject_new_url() -> None:
    original = (
        "See https://example.com/a for details about the release notes today "
        "and other related coverage from the same publisher this week."
    )
    entry = _entry("mid:1", original)
    report = _report(entry)
    # Same length-ish but introduces a new URL — should fail preservation.
    replacement = original.replace(
        "https://example.com/a",
        "https://example.com/a and https://evil.example/x",
    )
    result = _result(
        patches=(
            FinalReviewPatch(
                entry_id="mid:1",
                field="summary",
                replacement=replacement,
                rationale="bad",
                issue_id="iss-1",
            ),
        )
    )
    new_report, stats = apply_final_review_patches(
        report, result, _config(final_review_max_changed_chars_ratio=0.5)
    )
    assert stats.applied == 0
    assert stats.preservation_failed >= 1 or stats.rejected >= 1
    assert new_report.dated_by_folder["tech"][0].summary == original
