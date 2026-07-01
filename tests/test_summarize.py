"""Tests for Ollama summarisation helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rollup.classify import classify_message
from rollup.filter import make_digest_entry
from rollup.models import ParsedMessage
from rollup.parse import compute_content_hash
from rollup.summarize import (
    OllamaError,
    apply_summaries,
    build_prompt,
    check_ollama_available,
    is_local_ollama,
    validate_ollama_url,
)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _entry(body: str = "Newsletter body text for summarisation."):
    parsed = ParsedMessage(
        message_key="k1",
        content_hash=compute_content_hash(body),
        folder_name="tech",
        relative_folder_path="tech",
        subject="Weekly Update",
        sender="news@example.com",
        date_raw="",
        date_parsed=datetime.now().astimezone(),
        body_text=body,
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        read_time_minutes=2,
        preview=body[:100],
        parse_warnings=(),
    )
    return make_digest_entry(classify_message(parsed), no_ollama=False)


def test_validate_ollama_url_local() -> None:
    validate_ollama_url("http://localhost:11434/api/generate", allow_remote=False)


def test_validate_ollama_url_rejects_remote() -> None:
    with pytest.raises(OllamaError, match="not local"):
        validate_ollama_url("http://192.168.1.1:11434/api/generate", allow_remote=False)


def test_validate_ollama_url_allow_remote() -> None:
    validate_ollama_url("http://192.168.1.1:11434/api/generate", allow_remote=True)


def test_is_local_ollama() -> None:
    assert is_local_ollama("http://127.0.0.1:11434/api/generate")
    assert not is_local_ollama("http://example.com/api/generate")


def test_prompt_templates_exist() -> None:
    assert (PROMPTS_DIR / "_common.txt").is_file()
    for name in (
        "short_update",
        "multi_section_digest",
        "essay",
        "link_roundup",
        "unclassified",
    ):
        assert (PROMPTS_DIR / f"{name}.txt").is_file()


def test_build_prompt_includes_common() -> None:
    entry = _entry()
    prompt = build_prompt(entry.classified, entry.classified.parsed.body_text[:1000])
    common = (PROMPTS_DIR / "_common.txt").read_text(encoding="utf-8")
    assert common.strip()[:40] in prompt


@patch("requests.get")
def test_check_ollama_available_model_found(mock_get: MagicMock) -> None:
    pytest.importorskip("requests")
    mock_get.return_value.json.return_value = {"models": [{"name": "llama3.2:3b"}]}
    mock_get.return_value.raise_for_status = MagicMock()
    ok, msg = check_ollama_available("http://localhost:11434/api/generate", "llama3.2:3b")
    assert ok is True
    mock_get.assert_called_once()
    assert "/api/tags" in mock_get.call_args[0][0]
    assert "pull" not in mock_get.call_args[0][0]


@patch("requests.get")
def test_check_ollama_available_model_missing(mock_get: MagicMock) -> None:
    pytest.importorskip("requests")
    mock_get.return_value.json.return_value = {"models": [{"name": "other:7b"}]}
    mock_get.return_value.raise_for_status = MagicMock()
    ok, msg = check_ollama_available("http://localhost:11434/api/generate", "llama3.2:3b")
    assert ok is False


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
    from rollup.state import get_cached_summary, init_db_with_summaries, store_summary

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
