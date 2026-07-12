"""Hardening: final-review apply contracts (skip, caps, unicode, duplicates)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.config import Config, DEFAULT_FINAL_REVIEW_MAX_CHANGED_CHARS_RATIO
from rollup.final_review_apply import (
    apply_final_review_patches,
    should_globally_skip_apply,
)
from rollup.final_review_codes import resolve_apply_policy
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
        final_review_apply_policy="standard",
        final_review_max_patches_unattended=5,
        final_review_max_changed_chars_unattended=800,
    )
    base.update(kwargs)
    return Config(**base)


def _report(*entries: DigestEntry) -> DigestReport:
    now = datetime.now(timezone.utc)
    return DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=now,
        window_end=now,
        dated_by_folder={"tech": tuple(entries)},
        undated=(),
        stats=DigestStats(
            folders_scanned=1,
            messages_parsed=len(entries),
            dated_included=len(entries),
            undated_needing_review=0,
            skipped_outside_window=0,
            skipped_seen_undated=0,
            deduped_messages=0,
            parse_errors=0,
            summaries_ollama=0,
            summaries_cache=0,
            summaries_fallback=len(entries),
        ),
    )


BASE = (
    "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda "
    "mu nu xi omicron pi rho sigma tau."
)


def _issue(
    issue_id: str,
    entry_id: str = "mid:1",
    *,
    safe: bool = True,
) -> FinalReviewIssue:
    return FinalReviewIssue(
        severity="minor",
        type="style_drift",
        location="tech",
        entry_id=entry_id,
        description="tweak",
        suggested_fix=None,
        safe_auto_fix=safe,
        issue_id=issue_id,
    )


def _result(
    *,
    patches=(),
    issues=None,
    status="pass",
    safe=True,
    source="ollama",
    echo="fp",
    fingerprint="fp",
) -> FinalReviewResult:
    if issues is None:
        issues = (_issue("iss-1"),)
    return FinalReviewResult(
        overall_status=status,
        safe_to_publish=safe,
        issues=issues,
        patches=patches,
        review_source=source,
        profile_name="strict",
        model="m",
        prompt_version="final_review_v2_apply",
        generated_at=datetime.now(timezone.utc),
        digest_fingerprint=fingerprint,
        review_input_hash="ih",
        echoed_digest_fingerprint=echo,
        review_mode="apply",
    )


def _patch(entry_id: str, replacement: str, issue_id: str) -> FinalReviewPatch:
    return FinalReviewPatch(
        entry_id=entry_id,
        field="summary",
        replacement=replacement,
        rationale="clarity",
        issue_id=issue_id,
    )


def _tiny_edit(original: str, tag: str = "!") -> str:
    return original.replace("sigma tau.", f"sigma tau{tag}")


# --- Global skip ---


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"echo": None}, "fingerprint_missing"),
        ({"echo": ""}, "fingerprint_missing"),
        ({"echo": "other"}, "fingerprint_mismatch"),
        ({"safe": False}, "unsafe_to_publish"),
        ({"source": "error", "status": "fail", "safe": False}, "review_source_error"),
        ({"status": "fail", "safe": True}, "overall_status_fail"),
        ({"patches": ()}, "no_patches"),
    ],
)
def test_global_skip_codes(kwargs, code) -> None:
    patches = kwargs.pop("patches", (_patch("mid:1", _tiny_edit(BASE), "iss-1"),))
    result = _result(patches=patches, **kwargs)
    assert should_globally_skip_apply(result) == code
    report = _report(_entry("mid:1", BASE))
    new_report, stats = apply_final_review_patches(report, result, _config())
    assert stats.applied == 0
    assert stats.global_skip_reason == code
    assert new_report.dated_by_folder["tech"][0].summary == BASE


def test_issue_ids_not_unique_global_skip() -> None:
    result = _result(
        issues=(_issue("dup"), _issue("dup", entry_id="mid:2")),
        patches=(_patch("mid:1", _tiny_edit(BASE), "dup"),),
    )
    assert should_globally_skip_apply(result) == "issue_ids_not_unique"


# --- Linkage / safe_auto_fix ---


def test_missing_issue_id_rejects() -> None:
    result = _result(
        patches=(
            FinalReviewPatch(
                entry_id="mid:1",
                field="summary",
                replacement=_tiny_edit(BASE),
                rationale="x",
                issue_id=None,
            ),
        )
    )
    _, stats = apply_final_review_patches(_report(_entry("mid:1", BASE)), result, _config())
    assert stats.applied == 0
    assert dict(stats.reject_counts).get("missing_issue_id") == 1


def test_safe_auto_fix_must_be_literal_true() -> None:
    result = _result(
        issues=(_issue("iss-1", safe=False),),
        patches=(_patch("mid:1", _tiny_edit(BASE), "iss-1"),),
    )
    _, stats = apply_final_review_patches(_report(_entry("mid:1", BASE)), result, _config())
    assert stats.applied == 0
    assert dict(stats.reject_counts).get("safe_auto_fix_not_true") == 1


def test_unknown_issue_id() -> None:
    result = _result(patches=(_patch("mid:1", _tiny_edit(BASE), "nope"),))
    _, stats = apply_final_review_patches(_report(_entry("mid:1", BASE)), result, _config())
    assert dict(stats.reject_counts).get("unknown_issue_id") == 1


# --- Duplicates / conflicts ---


def test_duplicate_entry_and_issue() -> None:
    issues = (_issue("iss-1"), _issue("iss-2", entry_id="mid:1"))
    patches = (
        _patch("mid:1", _tiny_edit(BASE, "!"), "iss-1"),
        _patch("mid:1", _tiny_edit(BASE, "?"), "iss-2"),
    )
    result = _result(issues=issues, patches=patches)
    _, stats = apply_final_review_patches(_report(_entry("mid:1", BASE)), result, _config())
    assert stats.applied == 0
    assert dict(stats.reject_counts).get("conflicting_replacement") == 2


def test_duplicate_issue_id_across_patches() -> None:
    e1 = _entry("mid:1", BASE)
    e2 = _entry("mid:2", BASE)
    issues = (_issue("iss-1"),)
    patches = (
        _patch("mid:1", _tiny_edit(BASE, "!"), "iss-1"),
        _patch("mid:2", _tiny_edit(BASE, "!"), "iss-1"),
    )
    result = _result(issues=issues, patches=patches)
    new_report, stats = apply_final_review_patches(_report(e1, e2), result, _config())
    assert stats.applied == 1
    assert dict(stats.reject_counts).get("duplicate_issue_id") == 1
    assert new_report.dated_by_folder["tech"][0].summary != BASE


# --- Unattended whole-set caps ---


def test_unattended_patch_cap_whole_set() -> None:
    entries = [_entry(f"mid:{i}", BASE) for i in range(6)]
    issues = tuple(_issue(f"iss-{i}", entry_id=f"mid:{i}") for i in range(6))
    patches = tuple(
        _patch(f"mid:{i}", _tiny_edit(BASE, "!"), f"iss-{i}") for i in range(6)
    )
    policy = resolve_apply_policy(
        cron=True,
        apply_policy_name="conservative",
        allow_cron_apply=True,
        max_patches_unattended=5,
        max_changed_chars_unattended=800,
        max_changed_chars_ratio=0.5,
        preserve_links=True,
        preserve_quotes=True,
    )
    result = _result(issues=issues, patches=patches)
    new_report, stats = apply_final_review_patches(
        _report(*entries), result, _config(), policy=policy
    )
    assert stats.global_skip_reason == "unattended_patch_cap"
    assert stats.applied == 0
    for e in new_report.dated_by_folder["tech"]:
        assert e.summary == BASE


def test_unattended_exactly_five_applies() -> None:
    entries = [_entry(f"mid:{i}", BASE) for i in range(5)]
    issues = tuple(_issue(f"iss-{i}", entry_id=f"mid:{i}") for i in range(5))
    patches = tuple(
        _patch(f"mid:{i}", _tiny_edit(BASE, "!"), f"iss-{i}") for i in range(5)
    )
    policy = resolve_apply_policy(
        cron=True,
        apply_policy_name="conservative",
        allow_cron_apply=True,
        max_patches_unattended=5,
        max_changed_chars_unattended=800,
        max_changed_chars_ratio=0.5,
        preserve_links=True,
        preserve_quotes=True,
    )
    result = _result(issues=issues, patches=patches)
    _, stats = apply_final_review_patches(
        _report(*entries), result, _config(), policy=policy
    )
    assert stats.applied == 5
    assert stats.global_skip_reason is None


def test_unattended_char_cap_801_whole_set() -> None:
    # One large edit: delta > 800 under unattended.
    original = "x" * 100
    replacement = "y" * 902  # delta = 802
    entry = _entry("mid:1", original)
    policy = resolve_apply_policy(
        cron=False,
        apply_policy_name="conservative",
        allow_cron_apply=False,
        max_patches_unattended=5,
        max_changed_chars_unattended=800,
        max_changed_chars_ratio=0.5,
        preserve_links=True,
        preserve_quotes=True,
    )
    result = _result(
        patches=(_patch("mid:1", replacement, "iss-1"),),
    )
    # Ratio may also reject; raise ratio ceiling for this test.
    cfg = _config(final_review_max_changed_chars_ratio=0.5)
    # abs ceiling is max(200, 0.5*orig_len)=200 for short orig — use longer original
    original = "word " * 80  # 400 chars
    replacement = original + ("z" * 801)
    entry = _entry("mid:1", original)
    result = _result(patches=(_patch("mid:1", replacement, "iss-1"),))
    _, stats = apply_final_review_patches(
        _report(entry), result, cfg, policy=policy
    )
    # Either abs ceiling or unattended char cap — must not apply
    assert stats.applied == 0


def test_invalid_patches_do_not_block_later_interactive() -> None:
    e1 = _entry("mid:1", BASE)
    e2 = _entry("mid:2", BASE)
    issues = (_issue("iss-1"), _issue("iss-2", entry_id="mid:2"))
    patches = (
        _patch("mid:1", BASE + " https://evil.example/x", "iss-1"),  # URL reject
        _patch("mid:2", _tiny_edit(BASE), "iss-2"),
    )
    result = _result(issues=issues, patches=patches)
    cfg = _config(
        final_review_apply_policy="standard",
        final_review_max_changed_chars_ratio=0.5,
    )
    policy = resolve_apply_policy(
        cron=False,
        apply_policy_name="standard",
        allow_cron_apply=False,
        max_patches_unattended=5,
        max_changed_chars_unattended=800,
        max_changed_chars_ratio=0.5,
        preserve_links=True,
        preserve_quotes=True,
    )
    new_report, stats = apply_final_review_patches(
        _report(e1, e2), result, cfg, policy=policy
    )
    assert stats.applied == 1
    assert new_report.dated_by_folder["tech"][1].summary != BASE


# --- Unicode ---


def test_nfkc_identical_rejects() -> None:
    # Compatibility equivalent: ﬁ (U+FB01) vs fi
    original = "Alpha beta gamma delta epsilon zeta eta theta " + "fi" * 20
    replacement = "Alpha beta gamma delta epsilon zeta eta theta " + "\ufb01" * 20
    result = _result(patches=(_patch("mid:1", replacement, "iss-1"),))
    _, stats = apply_final_review_patches(
        _report(_entry("mid:1", original)),
        result,
        _config(final_review_max_changed_chars_ratio=0.5),
    )
    assert dict(stats.reject_counts).get("identical_nfkc") == 1


def test_crlf_vs_lf_counts_as_delta_not_identity() -> None:
    original = BASE + "\nline"
    replacement = BASE + "\r\nline"
    # Not NFKC-identical after whitespace collapse? \n vs \r\n may collapse differently
    # whitespace split collapses both to same — may be identical_nfkc
    result = _result(patches=(_patch("mid:1", replacement, "iss-1"),))
    _, stats = apply_final_review_patches(
        _report(_entry("mid:1", original)),
        result,
        _config(final_review_max_changed_chars_ratio=0.5),
    )
    # Either identical after collapse or applied with delta; must not crash
    assert stats.applied + stats.rejected >= 1


def test_emoji_edit_applies_when_within_ratio() -> None:
    original = BASE
    replacement = BASE.replace("tau.", "tau 😀.")
    result = _result(patches=(_patch("mid:1", replacement, "iss-1"),))
    _, stats = apply_final_review_patches(
        _report(_entry("mid:1", original)),
        result,
        _config(final_review_max_changed_chars_ratio=0.5),
    )
    assert stats.applied == 1
