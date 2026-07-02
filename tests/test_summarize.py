"""Tests for Ollama summarisation helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rollup.classify import classify_message
from rollup.filter import make_digest_entry
from rollup.models import ClassifiedMessage, ParsedMessage
from rollup.parse import compute_content_hash
from rollup.summarize import (
    PROMPT_VERSION,
    OllamaError,
    apply_summaries,
    build_prompt,
    build_summary_cache_key_parts,
    check_ollama_available,
    clean_summary_output,
    execute_summary_plan,
    is_local_ollama,
    summarize_message,
    validate_ollama_url,
)
from rollup.summary_plan import SummaryCliOptions, resolve_summary_plan
from rollup.summary_profiles import get_builtin_summary_profile_set

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
COMMON_SNIPPET = (PROMPTS_DIR / "_common.txt").read_text(encoding="utf-8").strip()[:40]

NEWSLETTER_TYPES = (
    "short_update",
    "multi_section_digest",
    "essay",
    "link_roundup",
    "unclassified",
)


def _parsed(
    body: str = "Newsletter body text for summarisation.",
    subject: str = "Weekly Update",
):
    return ParsedMessage(
        message_key="k1",
        content_hash=compute_content_hash(body),
        folder_name="tech",
        relative_folder_path="tech",
        subject=subject,
        sender="news@example.com",
        date_raw="",
        date_parsed=datetime.now().astimezone(),
        body_text=body,
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=2,
        preview=body[:100],
        parse_warnings=(),
    )


def _classified(
    newsletter_type: str, body: str = "Newsletter body text for summarisation."
):
    parsed = _parsed(body)
    return ClassifiedMessage(
        parsed=parsed,
        newsletter_type=newsletter_type,  # type: ignore[arg-type]
        classification_scores=(),
    )


def _entry(body: str = "Newsletter body text for summarisation."):
    return make_digest_entry(classify_message(_parsed(body)), no_ollama=False)


def test_validate_ollama_url_local() -> None:
    validate_ollama_url("http://localhost:11434/api/generate", allow_remote=False)


def test_validate_ollama_url_rejects_remote() -> None:
    with pytest.raises(OllamaError, match="not local"):
        validate_ollama_url("http://192.168.1.1:11434/api/generate", allow_remote=False)


def test_validate_ollama_url_rejects_missing_scheme() -> None:
    with pytest.raises(OllamaError, match="scheme"):
        validate_ollama_url("localhost:11434/api/generate", allow_remote=False)


def test_validate_ollama_url_rejects_missing_hostname() -> None:
    with pytest.raises(OllamaError, match="hostname"):
        validate_ollama_url("http:///api/generate", allow_remote=False)


def test_validate_ollama_url_allow_remote() -> None:
    validate_ollama_url("http://192.168.1.1:11434/api/generate", allow_remote=True)


def test_is_local_ollama() -> None:
    assert is_local_ollama("http://127.0.0.1:11434/api/generate")
    assert not is_local_ollama("http://example.com/api/generate")


def test_prompt_templates_exist() -> None:
    assert (PROMPTS_DIR / "_common.txt").is_file()
    for name in NEWSLETTER_TYPES:
        assert (PROMPTS_DIR / f"{name}.txt").is_file()


def test_build_prompt_common_once() -> None:
    entry = _entry()
    excerpt = entry.classified.parsed.body_text[:1000]
    prompt = build_prompt(entry.classified, excerpt)
    assert prompt.count(COMMON_SNIPPET) == 1
    assert entry.classified.parsed.subject in prompt
    assert excerpt in prompt


def test_build_prompt_style_changes_prompt() -> None:
    entry = _entry()
    excerpt = entry.classified.parsed.body_text[:1000]
    rough = build_prompt(entry.classified, excerpt, prompt_style="rough")
    deep = build_prompt(entry.classified, excerpt, prompt_style="deep")
    assert rough != deep


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Here's a summary of the newsletter in 2 bullets:\n- Point one\n- Point two",
            "- Point one\n- Point two",
        ),
        (
            "Summary:\n\n- First item",
            "- First item",
        ),
        (
            "Key points:\n- Alpha\n- Beta",
            "- Alpha\n- Beta",
        ),
        (
            "- Already clean\n- Second bullet",
            "- Already clean\n- Second bullet",
        ),
    ],
)
def test_clean_summary_output_strips_intro_lines(raw: str, expected: str) -> None:
    assert clean_summary_output(raw) == expected


def test_build_prompt_discourages_intro_filler() -> None:
    prompt = build_prompt(_entry().classified, "body excerpt")
    assert "no preamble" in prompt.lower() or "no intro" in prompt.lower()


@pytest.mark.parametrize("newsletter_type", NEWSLETTER_TYPES)
def test_build_prompt_all_types(newsletter_type: str) -> None:
    classified = _classified(newsletter_type)
    excerpt = classified.parsed.body_text[:500]
    prompt = build_prompt(classified, excerpt)
    assert prompt.count(COMMON_SNIPPET) == 1
    assert classified.parsed.subject in prompt
    assert excerpt in prompt


@patch("requests.get")
def test_check_ollama_available_model_found(mock_get: MagicMock) -> None:
    pytest.importorskip("requests")
    mock_get.return_value.json.return_value = {"models": [{"name": "llama3.2:3b"}]}
    mock_get.return_value.raise_for_status = MagicMock()
    ok, msg = check_ollama_available(
        "http://localhost:11434/api/generate", "llama3.2:3b"
    )
    assert ok is True
    mock_get.assert_called_once()
    assert "/api/tags" in mock_get.call_args[0][0]
    assert "pull" not in mock_get.call_args[0][0]


@patch("requests.get")
def test_check_ollama_available_model_missing(mock_get: MagicMock) -> None:
    pytest.importorskip("requests")
    mock_get.return_value.json.return_value = {"models": [{"name": "other:7b"}]}
    mock_get.return_value.raise_for_status = MagicMock()
    ok, msg = check_ollama_available(
        "http://localhost:11434/api/generate", "llama3.2:3b"
    )
    assert ok is False


@patch("requests.get")
def test_check_ollama_available_bare_name_matches_tagged(mock_get: MagicMock) -> None:
    pytest.importorskip("requests")
    mock_get.return_value.json.return_value = {"models": [{"name": "llama3.2:latest"}]}
    mock_get.return_value.raise_for_status = MagicMock()
    ok, _ = check_ollama_available("http://localhost:11434/api/generate", "llama3.2")
    assert ok is True


@patch("requests.get")
def test_check_ollama_available_rejects_partial_name_match(mock_get: MagicMock) -> None:
    pytest.importorskip("requests")
    mock_get.return_value.json.return_value = {"models": [{"name": "llama3.2:3b"}]}
    mock_get.return_value.raise_for_status = MagicMock()
    ok, _ = check_ollama_available("http://localhost:11434/api/generate", "llama")
    assert ok is False


@patch("requests.post")
def test_summarize_message_posts_generate_payload(mock_post: MagicMock) -> None:
    pytest.importorskip("requests")
    mock_post.return_value.json.return_value = {"response": "Bullet summary"}
    mock_post.return_value.raise_for_status = MagicMock()
    entry = _entry()
    result = summarize_message(
        entry.classified,
        "http://localhost:11434/api/generate",
        "llama3.2:3b",
        30000,
        quiet=True,
    )
    assert result == "Bullet summary"
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert payload["model"] == "llama3.2:3b"
    assert payload["stream"] is False
    assert "prompt" in payload
    assert entry.classified.parsed.subject in payload["prompt"]
    assert payload["options"]["temperature"] == 0.2


@patch("requests.post")
def test_summarize_message_streams_when_not_quiet(mock_post: MagicMock) -> None:
    pytest.importorskip("requests")
    mock_post.return_value.raise_for_status = MagicMock()
    mock_post.return_value.iter_lines.return_value = [
        json.dumps({"response": "Bullet ", "done": False}),
        json.dumps({"response": "summary", "done": True, "eval_count": 2}),
    ]
    entry = _entry()
    result = summarize_message(
        entry.classified,
        "http://localhost:11434/api/generate",
        "llama3.2:3b",
        30000,
        quiet=False,
    )
    assert result == "Bullet summary"
    payload = mock_post.call_args.kwargs["json"]
    assert payload["stream"] is True
    assert mock_post.call_args.kwargs["stream"] is True


@patch("rollup.summarize.summarize_message")
@patch("rollup.summarize.check_ollama_available")
def test_apply_summaries_continues_after_one_failure(
    mock_check: MagicMock, mock_summarize: MagicMock
) -> None:
    mock_check.return_value = (True, "ok")
    mock_summarize.side_effect = [RuntimeError("timeout"), "Bullet summary"]
    entries = [_entry("body one"), _entry("body two")]
    result = apply_summaries(
        entries,
        "http://localhost:11434/api/generate",
        "llama3.2:3b",
        30000,
        allow_remote=False,
    )
    assert len(result) == 2
    assert result[0].summary_source == "preview_fallback"
    assert result[1].summary_source == "ollama"
    assert result[1].summary == "Bullet summary"


@patch("rollup.summarize.check_ollama_available")
def test_apply_summaries_fallback_when_unavailable(mock_check: MagicMock) -> None:
    mock_check.return_value = (False, "connection refused")
    entries = [_entry()]
    result = apply_summaries(
        entries,
        "http://localhost:11434/api/generate",
        "llama3.2:3b",
        30000,
        allow_remote=False,
    )
    assert result[0].summary_source == "preview_fallback"


@patch("rollup.summarize.summarize_message")
@patch("rollup.summarize.check_ollama_available")
def test_apply_summaries_rebuild_bypasses_cache(
    mock_check: MagicMock, mock_summarize: MagicMock, tmp_path
) -> None:
    from rollup.state import init_db_with_summaries, store_summary

    mock_check.return_value = (True, "ok")
    mock_summarize.return_value = "Fresh summary"
    conn = init_db_with_summaries(tmp_path / "rollup.db")
    parsed = _entry().classified.parsed
    store_summary(
        conn,
        parsed.message_key,
        parsed.content_hash,
        "short_update",
        "llama3.2:3b",
        "Cached old",
        datetime.now().astimezone(),
    )
    entries = [_entry()]
    result = apply_summaries(
        entries,
        "http://localhost:11434/api/generate",
        "llama3.2:3b",
        30000,
        allow_remote=False,
        conn=conn,
        rebuild=False,
    )
    assert result[0].summary_source == "cache"
    assert result[0].summary == "Cached old"

    mock_summarize.reset_mock()
    result2 = apply_summaries(
        entries,
        "http://localhost:11434/api/generate",
        "llama3.2:3b",
        30000,
        allow_remote=False,
        conn=conn,
        rebuild=True,
    )
    assert result2[0].summary_source == "ollama"
    mock_summarize.assert_called_once()


@patch("rollup.summarize.summarize_message")
@patch("rollup.summarize.check_ollama_available")
def test_apply_summaries_model_change_cache_miss(
    mock_check: MagicMock, mock_summarize: MagicMock, tmp_path
) -> None:
    from rollup.state import init_db_with_summaries, store_summary

    mock_check.return_value = (True, "ok")
    mock_summarize.return_value = "New model summary"
    conn = init_db_with_summaries(tmp_path / "rollup.db")
    parsed = _entry().classified.parsed
    store_summary(
        conn,
        parsed.message_key,
        parsed.content_hash,
        "short_update",
        "llama3.2:3b",
        "Cached old",
        datetime.now().astimezone(),
    )
    entries = [_entry()]
    result = apply_summaries(
        entries,
        "http://localhost:11434/api/generate",
        "other:7b",
        30000,
        allow_remote=False,
        conn=conn,
        rebuild=False,
    )
    assert result[0].summary_source == "ollama"
    assert result[0].summary == "New model summary"
    mock_summarize.assert_called_once()


@patch("rollup.summarize.summarize_message")
@patch("rollup.summarize.check_ollama_available")
def test_apply_summaries_switching_back_to_model_a_hits_cache(
    mock_check: MagicMock, mock_summarize: MagicMock, tmp_path
) -> None:
    from rollup.state import init_db_with_summaries

    mock_check.return_value = (True, "ok")
    mock_summarize.side_effect = ["Model A summary", "Model B summary"]
    conn = init_db_with_summaries(tmp_path / "rollup.db")
    entries = [_entry()]
    common = {
        "ollama_url": "http://localhost:11434/api/generate",
        "max_chars": 30000,
        "allow_remote": False,
        "conn": conn,
        "rebuild": False,
    }

    result_a = apply_summaries(entries, model="llama3.2:3b", **common)
    assert result_a[0].summary_source == "ollama"
    assert result_a[0].summary == "Model A summary"

    result_b = apply_summaries(entries, model="other:7b", **common)
    assert result_b[0].summary_source == "ollama"
    assert result_b[0].summary == "Model B summary"
    assert mock_summarize.call_count == 2

    mock_summarize.reset_mock()
    result_a2 = apply_summaries(entries, model="llama3.2:3b", **common)
    assert result_a2[0].summary_source == "cache"
    assert result_a2[0].summary == "Model A summary"
    mock_summarize.assert_not_called()


@patch("rollup.state.store_summary")
@patch("rollup.summarize.summarize_message")
@patch("rollup.summarize.check_ollama_available")
def test_apply_summaries_store_failure_keeps_ollama_summary(
    mock_check: MagicMock, mock_summarize: MagicMock, mock_store: MagicMock, tmp_path
) -> None:
    from rollup.state import init_db_with_summaries

    mock_check.return_value = (True, "ok")
    mock_summarize.return_value = "Fresh summary"
    mock_store.side_effect = RuntimeError("disk full")
    conn = init_db_with_summaries(tmp_path / "rollup.db")
    entries = [_entry()]
    result = apply_summaries(
        entries,
        "http://localhost:11434/api/generate",
        "llama3.2:3b",
        30000,
        allow_remote=False,
        conn=conn,
    )
    assert result[0].summary_source == "ollama"
    assert result[0].summary == "Fresh summary"


@patch("rollup.state.get_cached_summary")
@patch("rollup.summarize.summarize_message")
@patch("rollup.summarize.check_ollama_available")
def test_apply_summaries_cache_read_failure_continues(
    mock_check: MagicMock,
    mock_summarize: MagicMock,
    mock_get_cached: MagicMock,
    tmp_path,
) -> None:
    from rollup.state import init_db_with_summaries

    mock_check.return_value = (True, "ok")
    mock_summarize.return_value = "Fresh summary"
    mock_get_cached.side_effect = RuntimeError("db locked")
    conn = init_db_with_summaries(tmp_path / "rollup.db")
    entries = [_entry()]
    result = apply_summaries(
        entries,
        "http://localhost:11434/api/generate",
        "llama3.2:3b",
        30000,
        allow_remote=False,
        conn=conn,
    )

    assert result[0].summary_source == "ollama"
    assert result[0].summary == "Fresh summary"
    mock_summarize.assert_called_once()


@patch("rollup.summarize.summarize_message")
def test_execute_summary_plan_legacy_cache_reused(
    mock_summarize: MagicMock, tmp_path
) -> None:
    from rollup.state import init_db_with_summaries, store_summary

    conn = init_db_with_summaries(tmp_path / "rollup.db")
    entry = _entry()
    parsed = entry.classified.parsed
    store_summary(
        conn,
        parsed.message_key,
        parsed.content_hash,
        entry.classified.newsletter_type,
        "qwen2.5:7b",
        "Legacy summary",
        datetime.now().astimezone(),
    )
    plan = resolve_summary_plan(
        [entry],
        get_builtin_summary_profile_set(),
        SummaryCliOptions(summary_profile="standard"),
    )
    execution = execute_summary_plan(
        entries=[entry],
        plan=plan,
        ollama_url="http://localhost:11434/api/generate",
        default_model="llama3.2:3b",
        max_chars=30000,
        allow_remote=False,
        conn=conn,
    )
    assert execution.entries_by_variant["default"][0].summary == "Legacy summary"
    mock_summarize.assert_not_called()


@patch("rollup.summarize.check_ollama_available")
def test_execute_summary_plan_missing_model_falls_back(mock_check: MagicMock) -> None:
    mock_check.return_value = (False, "missing")
    entry = _entry()
    plan = resolve_summary_plan(
        [entry],
        get_builtin_summary_profile_set(),
        SummaryCliOptions(summary_profile="standard"),
    )
    execution = execute_summary_plan(
        entries=[entry],
        plan=plan,
        ollama_url="http://localhost:11434/api/generate",
        default_model="llama3.2:3b",
        max_chars=30000,
        allow_remote=False,
    )
    result = execution.entries_by_variant["default"][0]
    assert result.summary_source == "preview_fallback"


def test_summary_cache_key_parts_change_with_options() -> None:
    common = dict(
        message_key="k1",
        content_hash="hash1",
        newsletter_type="short_update",
        provider="ollama",
        profile_name="rough",
        model="llama3.2:3b",
        prompt_style="rough",
        prompt_version=PROMPT_VERSION,
        temperature=0.2,
        num_ctx=8192,
        options={"top_p": 0.9},
        summary_input_hash="input-hash-1",
    )
    base = build_summary_cache_key_parts(**common)
    assert base != build_summary_cache_key_parts(
        **{**common, "options": {"top_p": 0.8}}
    )
