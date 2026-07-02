"""Tests for final digest review."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from rollup.classify import classify_message
from rollup.config import Config, compute_date_window
from rollup.filter import make_digest_entry
from rollup.final_review import (
    FINAL_REVIEW_PROMPT_VERSION,
    build_final_review_prompt,
    build_review_corpus,
    compute_digest_fingerprint,
    compute_review_input_hash,
    execute_final_review,
    parse_final_review_response,
    write_final_review_report,
)
from rollup.final_review_profiles import (
    FinalReviewConfigError,
    resolve_final_review_profile,
    validate_final_review_config,
)
from rollup.models import DigestReport, DigestStats, LinkItem, ParsedMessage
from rollup.parse import compute_content_hash
from rollup.state import (
    SCHEMA_VERSION,
    get_final_review_generation,
    get_schema_version,
    init_db_with_summaries,
    store_final_review_generation,
)


def _parsed(
    *,
    message_key: str = "k1",
    subject: str = "Test Subject",
    body: str = "Body",
    folder: str = "tech",
) -> ParsedMessage:
    return ParsedMessage(
        message_key=message_key,
        content_hash=compute_content_hash(body),
        folder_name=folder,
        relative_folder_path=folder,
        subject=subject,
        sender="a@example.com",
        date_raw="",
        date_parsed=datetime.now().astimezone(),
        body_text=body,
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=("https://example.com",),
        link_items=(LinkItem("https://example.com", "Example", None, 0),),
        read_time_minutes=2,
        preview=body[:100],
        parse_warnings=(),
    )


def _entry(
    *,
    message_key: str = "k1",
    subject: str = "Test Subject",
    summary: str = "- bullet one",
    folder: str = "tech",
) -> "DigestEntry":
    from rollup.models import DigestEntry

    parsed = _parsed(message_key=message_key, subject=subject, folder=folder)
    classified = classify_message(parsed)
    return DigestEntry(
        classified=classified,
        summary=summary,
        summary_source="preview_fallback",
    )


def _report(
  entries_by_folder: dict[str, tuple] | None = None,
  undated: tuple = (),
) -> DigestReport:
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)
    if entries_by_folder is None:
        entries_by_folder = {"tech": (_entry(),)}
    stats = DigestStats(
        folders_scanned=1,
        messages_parsed=1,
        dated_included=sum(len(v) for v in entries_by_folder.values()),
        undated_needing_review=len(undated),
        skipped_outside_window=0,
        skipped_seen_undated=0,
        deduped_messages=0,
        parse_errors=0,
        summaries_ollama=0,
        summaries_cache=0,
        summaries_fallback=1,
    )
    return DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder=entries_by_folder,
        undated=undated,
        stats=stats,
    )


def _config(tmp_path: Path, **overrides) -> Config:
    base = dict(
        root=tmp_path,
        mail_root=tmp_path / "mail",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        lookback_days=7,
        folders_include=(),
        folders_exclude=(),
        dry_run=False,
        no_ollama=True,
        include_seen_undated=False,
        rebuild_summaries=False,
        max_body_chars=200_000,
        max_chars_for_llm=30_000,
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
        verbose=False,
        quiet=True,
        final_review_enabled=True,
        final_review_mode="report",
        final_review_profile="strict",
        final_review_provider="ollama",
        final_review_model=None,
        final_review_report_path=None,
        rebuild_final_review=False,
        final_review_preserve_links=True,
        final_review_preserve_quotes=True,
        final_review_max_changed_chars_ratio=0.08,
    )
    base.update(overrides)
    return Config(**base)


def test_build_review_corpus_ordering() -> None:
    report = _report(
        entries_by_folder={
            "misc": (_entry(message_key="m1", subject="M"),),
            "tech": (_entry(message_key="t1", subject="T"),),
        },
        undated=(_entry(message_key="u1", subject="U"),),
    )
    corpus = build_review_corpus(report)
    sections = [entry.section for entry in corpus.entries]
    assert sections == ["misc", "tech", "undated"]
    assert corpus.entry_count == 3
    assert corpus.entries[0].link_labels == ("Example",)


def test_digest_fingerprint_stable() -> None:
    report = _report()
    assert compute_digest_fingerprint(report) == compute_digest_fingerprint(report)


def test_digest_fingerprint_changes_with_summary() -> None:
    report_a = _report(entries_by_folder={"tech": (_entry(summary="- a"),)})
    report_b = _report(entries_by_folder={"tech": (_entry(summary="- b"),)})
    assert compute_digest_fingerprint(report_a) != compute_digest_fingerprint(
        report_b
    )


def test_review_input_hash_changes_with_profile() -> None:
    report = _report()
    corpus = build_review_corpus(report)
    strict = resolve_final_review_profile("strict")
    concise = resolve_final_review_profile("concise")
    prompt = build_final_review_prompt(corpus, strict)
    hash_strict = compute_review_input_hash(corpus, strict, prompt)
    hash_concise = compute_review_input_hash(
        corpus, concise, build_final_review_prompt(corpus, concise)
    )
    assert hash_strict != hash_concise


def test_parse_final_review_response_success() -> None:
    raw = json.dumps(
        {
            "overall_status": "pass_with_warnings",
            "safe_to_publish": True,
            "issues": [
                {
                    "severity": "minor",
                    "type": "style_drift",
                    "location": "tech / Test",
                    "entry_id": "k1",
                    "description": "Mixed bullets",
                    "suggested_fix": "Normalize bullets",
                    "safe_auto_fix": False,
                }
            ],
            "patches": [],
        }
    )
    result = parse_final_review_response(
        raw,
        profile_name="strict",
        model="qwen2.5:7b",
        generated_at=datetime.now().astimezone(),
        digest_fingerprint="abc",
        review_input_hash="def",
    )
    assert result.overall_status == "pass_with_warnings"
    assert result.issues[0].type == "style_drift"
    assert result.patches == ()


def test_parse_final_review_unknown_type_coerced() -> None:
    raw = json.dumps(
        {
            "overall_status": "pass",
            "safe_to_publish": True,
            "issues": [
                {
                    "severity": "minor",
                    "type": "unknown_type",
                    "location": "x",
                    "entry_id": None,
                    "description": "issue",
                    "safe_auto_fix": False,
                }
            ],
            "patches": [],
        }
    )
    result = parse_final_review_response(
        raw,
        profile_name="strict",
        model="qwen2.5:7b",
        generated_at=datetime.now().astimezone(),
        digest_fingerprint="abc",
        review_input_hash="def",
    )
    assert result.issues[0].type == "other"


def test_parse_final_review_malformed_json() -> None:
    result = parse_final_review_response(
        "not json",
        profile_name="strict",
        model="qwen2.5:7b",
        generated_at=datetime.now().astimezone(),
        digest_fingerprint="abc",
        review_input_hash="def",
    )
    assert result.review_source == "error"
    assert result.overall_status == "fail"
    assert result.issues[0].severity == "critical"


def test_write_final_review_report(tmp_path: Path) -> None:
    result = parse_final_review_response(
        json.dumps(
            {
                "overall_status": "pass",
                "safe_to_publish": True,
                "issues": [],
                "patches": [],
            }
        ),
        profile_name="strict",
        model="qwen2.5:7b",
        generated_at=datetime.now().astimezone(),
        digest_fingerprint="abc",
        review_input_hash="def",
    )
    path = tmp_path / "review.json"
    write_final_review_report(result, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["overall_status"] == "pass"
    assert data["prompt_version"] == FINAL_REVIEW_PROMPT_VERSION


def test_validate_apply_mode_blocked() -> None:
    with pytest.raises(FinalReviewConfigError, match="apply mode is not implemented"):
        validate_final_review_config(
            mode="apply", provider="ollama", profile_name="strict"
        )


@patch("rollup.final_review.call_final_review_model")
def test_execute_final_review_cache_roundtrip(
    mock_call, tmp_path: Path
) -> None:
    mock_call.return_value = json.dumps(
        {
            "overall_status": "pass",
            "safe_to_publish": True,
            "issues": [],
            "patches": [],
        }
    )
    report = _report()
    config = _config(tmp_path)
    conn = init_db_with_summaries(config.db_path)
    assert get_schema_version(conn) == SCHEMA_VERSION

    first = execute_final_review(report, config, conn=conn)
    assert first.review_source == "ollama"
    assert mock_call.call_count == 1

    second = execute_final_review(report, config, conn=conn)
    assert second.review_source == "cache"
    assert mock_call.call_count == 1


def test_final_review_cache_key_dimensions(tmp_path: Path) -> None:
    conn = init_db_with_summaries(tmp_path / "rollup.db")
    store_final_review_generation(
        conn,
        digest_fingerprint="fp",
        review_input_hash="ih",
        provider="ollama",
        profile_name="strict",
        model="qwen2.5:7b",
        prompt_version=FINAL_REVIEW_PROMPT_VERSION,
        temperature=0.1,
        num_ctx=8192,
        options={"format": "json"},
        result_json='{"overall_status":"pass"}',
        created_at=datetime.now().astimezone(),
    )
    cached = get_final_review_generation(
        conn,
        digest_fingerprint="fp",
        review_input_hash="ih",
        provider="ollama",
        profile_name="strict",
        model="qwen2.5:7b",
        prompt_version=FINAL_REVIEW_PROMPT_VERSION,
        temperature=0.1,
        num_ctx=8192,
        options={"format": "json"},
    )
    assert cached is not None
