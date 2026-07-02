"""Tests for summary profile and cache hardening."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rollup.cache_keys import canonicalize_provider_options
from rollup.filter import make_digest_entry
from rollup.models import ClassifiedMessage, ParsedMessage
from rollup.parse import compute_content_hash
from rollup.state import (
    SCHEMA_VERSION,
    get_cached_summary_generation,
    get_schema_version,
    init_db,
    init_db_with_summaries,
    store_summary,
    store_summary_generation,
)
from rollup.summarize import (
    PROMPT_VERSION,
    OllamaAvailabilityCache,
    SummarizeMessageResult,
    build_summary_cache_key_parts,
    compute_summary_input_hash,
    execute_summary_plan,
    legacy_cache_compatible,
)
from rollup.summary_plan import SummaryCliOptions, resolve_summary_plan
from rollup.summary_profiles import (
    DisabledSummaryProfileError,
    SummaryConfigError,
    get_builtin_summary_profile_set,
    load_summary_profile_set,
    require_valid_summary_profile_set,
    summary_profile_set_from_dict,
    summary_profile_set_to_dict,
    validate_summary_profile_set,
)


def _entry(
    body: str = "Newsletter body text for summarisation.",
    newsletter_type: str = "essay",
):
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
        link_items=(),
        read_time_minutes=2,
        preview=body[:100],
        parse_warnings=(),
    )
    classified = ClassifiedMessage(
        parsed=parsed,
        newsletter_type=newsletter_type,  # type: ignore[arg-type]
        classification_scores=(),
    )
    return make_digest_entry(classified, no_ollama=False)


def test_summary_input_hash_separates_excerpt_and_max_chars() -> None:
    entry = _entry("alpha " * 1000)
    hash_default = compute_summary_input_hash(
        entry.classified, prompt_style="standard", max_chars=30000
    )
    hash_short = compute_summary_input_hash(
        entry.classified, prompt_style="standard", max_chars=100
    )
    hash_deep = compute_summary_input_hash(
        entry.classified, prompt_style="deep", max_chars=30000
    )
    assert hash_default != hash_short
    assert hash_default != hash_deep


def test_rich_cache_misses_when_summary_input_hash_differs(tmp_path: Path) -> None:
    conn = init_db_with_summaries(tmp_path / "rollup.db")
    entry = _entry()
    input_hash = compute_summary_input_hash(
        entry.classified, prompt_style="standard", max_chars=30000
    )
    now = datetime.now().astimezone()
    store_summary_generation(
        conn,
        message_key=entry.classified.parsed.message_key,
        content_hash=entry.classified.parsed.content_hash,
        newsletter_type=entry.classified.newsletter_type,
        provider="ollama",
        profile_name="standard",
        model="qwen2.5:7b",
        prompt_style="standard",
        prompt_version=PROMPT_VERSION,
        temperature=0.2,
        num_ctx=16384,
        options={},
        summary_input_hash=input_hash,
        summary="Cached with matching input",
        created_at=now,
    )
    other_hash = compute_summary_input_hash(
        entry.classified, prompt_style="standard", max_chars=100
    )
    assert other_hash != input_hash
    assert (
        get_cached_summary_generation(
            conn,
            message_key=entry.classified.parsed.message_key,
            content_hash=entry.classified.parsed.content_hash,
            newsletter_type=entry.classified.newsletter_type,
            provider="ollama",
            profile_name="standard",
            model="qwen2.5:7b",
            prompt_style="standard",
            prompt_version=PROMPT_VERSION,
            temperature=0.2,
            num_ctx=16384,
            options={},
            summary_input_hash=other_hash,
        )
        is None
    )


def test_legacy_cache_only_for_standard_profile(tmp_path: Path) -> None:
    from rollup.state import init_db_with_summaries

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

    deep_plan = resolve_summary_plan(
        [entry],
        get_builtin_summary_profile_set(),
        SummaryCliOptions(summary_profile="deep"),
    )
    with patch(
        "rollup.summarize.summarize_message",
        return_value=SummarizeMessageResult(
            text="Fresh deep summary",
            stop_reason="done",
            output_chars=18,
            elapsed_seconds=0.1,
            body_chars=10,
            prompt_chars=100,
            link_count=0,
        ),
    ) as mock_summarize:
        with patch(
            "rollup.summarize.check_ollama_available", return_value=(True, "ok")
        ):
            deep_execution = execute_summary_plan(
                entries=[entry],
                plan=deep_plan,
                ollama_url="http://localhost:11434/api/generate",
                default_model="llama3.2:3b",
                max_chars=30000,
                allow_remote=False,
                conn=conn,
            )
    assert (
        deep_execution.entries_by_variant["default"][0].summary == "Fresh deep summary"
    )
    mock_summarize.assert_called_once()

    standard_plan = resolve_summary_plan(
        [entry],
        get_builtin_summary_profile_set(),
        SummaryCliOptions(summary_profile="standard"),
    )
    with patch("rollup.summarize.summarize_message") as mock_summarize:
        standard_execution = execute_summary_plan(
            entries=[entry],
            plan=standard_plan,
            ollama_url="http://localhost:11434/api/generate",
            default_model="llama3.2:3b",
            max_chars=30000,
            allow_remote=False,
            conn=conn,
        )
    assert (
        standard_execution.entries_by_variant["default"][0].summary == "Legacy summary"
    )
    mock_summarize.assert_not_called()


def test_legacy_cache_compatible_helper() -> None:
    profile_set = get_builtin_summary_profile_set()
    standard_plan = resolve_summary_plan(
        [_entry()],
        profile_set,
        SummaryCliOptions(summary_profile="standard"),
    )
    deep_plan = resolve_summary_plan(
        [_entry()],
        profile_set,
        SummaryCliOptions(summary_profile="deep"),
    )
    assert legacy_cache_compatible(standard_plan.jobs_by_variant["default"][0]) is True
    assert legacy_cache_compatible(deep_plan.jobs_by_variant["default"][0]) is False


def test_disabled_profile_rejected_in_validation() -> None:
    profile_set = summary_profile_set_from_dict(
        summary_profile_set_to_dict(get_builtin_summary_profile_set())
    )
    profile_set.profiles["standard"] = replace(
        profile_set.profiles["standard"], enabled=False
    )
    issues = validate_summary_profile_set(profile_set)
    assert any(issue.code == "disabled_profile_reference" for issue in issues)
    with pytest.raises(SummaryConfigError, match="disabled"):
        require_valid_summary_profile_set(profile_set)


def test_disabled_profile_rejected_in_plan_resolution() -> None:
    profile_set = summary_profile_set_from_dict(
        summary_profile_set_to_dict(get_builtin_summary_profile_set())
    )
    profile_set.profiles["rough"] = replace(
        profile_set.profiles["rough"], enabled=False
    )
    with pytest.raises(DisabledSummaryProfileError, match="rough"):
        resolve_summary_plan(
            [_entry("body", "link_roundup")],
            profile_set,
            SummaryCliOptions(summary_profile="rough"),
        )


def test_strict_profile_set_loading_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SummaryConfigError, match="not found"):
        load_summary_profile_set(tmp_path / "missing.json")


def test_strict_profile_set_loading_empty_profiles(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text(
        json.dumps({"profiles": {}, "default_profile": "standard"}), encoding="utf-8"
    )
    with pytest.raises(SummaryConfigError, match="no profiles"):
        load_summary_profile_set(path)


def test_strict_profile_set_loading_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SummaryConfigError, match="valid JSON"):
        load_summary_profile_set(path)


def test_strict_profile_set_loading_uses_exact_file(tmp_path: Path) -> None:
    path = tmp_path / "custom.json"
    data = summary_profile_set_to_dict(get_builtin_summary_profile_set())
    data["default_profile"] = "rough"
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_summary_profile_set(path)
    assert loaded.default_profile == "rough"
    assert loaded.profiles["rough"].model == "llama3.2:3b"


@patch("rollup.summarize.check_ollama_available")
def test_ollama_availability_cached_per_run(mock_check: MagicMock) -> None:
    mock_check.return_value = (True, "ok")
    cache = OllamaAvailabilityCache("http://localhost:11434/api/generate")
    assert cache.check("model-a") == (True, "ok")
    assert cache.check("model-a") == (True, "ok")
    assert cache.check("model-b") == (True, "ok")
    assert mock_check.call_count == 2


@patch("rollup.summarize.summarize_message")
@patch("rollup.summarize.check_ollama_available")
def test_execute_summary_plan_checks_each_model_once(
    mock_check: MagicMock, mock_summarize: MagicMock, tmp_path: Path
) -> None:
    mock_check.return_value = (True, "ok")
    mock_summarize.return_value = SummarizeMessageResult(
        text="Summary",
        stop_reason="done",
        output_chars=7,
        elapsed_seconds=0.1,
        body_chars=10,
        prompt_chars=100,
        link_count=0,
    )
    conn = init_db_with_summaries(tmp_path / "rollup.db")
    entries = [_entry("one", "essay"), _entry("two", "link_roundup")]
    plan = resolve_summary_plan(
        entries,
        get_builtin_summary_profile_set(),
        SummaryCliOptions(summary_type_routing=True),
    )
    execute_summary_plan(
        entries=entries,
        plan=plan,
        ollama_url="http://localhost:11434/api/generate",
        default_model="llama3.2:3b",
        max_chars=30000,
        allow_remote=False,
        conn=conn,
        rebuild=True,
    )
    models_checked = {call.args[1] for call in mock_check.call_args_list}
    assert models_checked == {"gpt-oss:20b", "llama3.2:3b"}
    assert mock_check.call_count == 2


def test_schema_version_singleton(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    assert get_schema_version(conn) == SCHEMA_VERSION
    rows = conn.execute("SELECT id, version FROM schema_version").fetchall()
    assert rows == [(1, SCHEMA_VERSION)]
    conn.close()

    conn = init_db(db)
    assert get_schema_version(conn) == SCHEMA_VERSION
    rows = conn.execute("SELECT id, version FROM schema_version").fetchall()
    assert rows == [(1, SCHEMA_VERSION)]
    conn.close()


def test_schema_version_migrates_legacy_multi_row_table(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    conn.execute("DROP TABLE schema_version")
    conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
    conn.executemany(
        "INSERT INTO schema_version (version) VALUES (?)",
        [(1,), (2,), (3,)],
    )
    conn.commit()
    conn.close()

    conn = init_db_with_summaries(tmp_path / "rollup.db")
    assert get_schema_version(conn) == SCHEMA_VERSION
    rows = conn.execute("SELECT id, version FROM schema_version").fetchall()
    assert rows == [(1, SCHEMA_VERSION)]


def test_canonical_provider_options_stable_for_nested_dicts() -> None:
    options_a = {"sampler": {"top_k": 40, "top_p": 0.9}}
    options_b = {"sampler": {"top_p": 0.9, "top_k": 40}}
    assert canonicalize_provider_options(options_a) == canonicalize_provider_options(
        options_b
    )
    key_a = build_summary_cache_key_parts(
        message_key="k1",
        content_hash="hash1",
        newsletter_type="essay",
        provider="ollama",
        profile_name="deep",
        model="gpt-oss:20b",
        prompt_style="deep",
        prompt_version=PROMPT_VERSION,
        temperature=0.2,
        num_ctx=32768,
        options=options_a,
        summary_input_hash="input-hash",
    )
    key_b = build_summary_cache_key_parts(
        message_key="k1",
        content_hash="hash1",
        newsletter_type="essay",
        provider="ollama",
        profile_name="deep",
        model="gpt-oss:20b",
        prompt_style="deep",
        prompt_version=PROMPT_VERSION,
        temperature=0.2,
        num_ctx=32768,
        options=options_b,
        summary_input_hash="input-hash",
    )
    assert key_a == key_b


@patch("rollup.state.store_summary_generation")
@patch("rollup.summarize.summarize_message")
@patch("rollup.summarize.check_ollama_available")
def test_overlong_stream_fallback_not_cached_reported(
    mock_check: MagicMock,
    mock_summarize: MagicMock,
    mock_store: MagicMock,
    tmp_path: Path,
) -> None:
    mock_check.return_value = (True, "ok")
    mock_summarize.return_value = SummarizeMessageResult(
        text="x" * 2000,
        stop_reason="local_char_cap",
        output_chars=2000,
        elapsed_seconds=1.5,
        body_chars=120,
        prompt_chars=450,
        link_count=3,
    )
    conn = init_db_with_summaries(tmp_path / "rollup.db")
    entry = _entry("body text for overlong stream test", "link_roundup")
    plan = resolve_summary_plan(
        [entry],
        get_builtin_summary_profile_set(),
        SummaryCliOptions(summary_type_routing=True),
    )
    execution = execute_summary_plan(
        entries=[entry],
        plan=plan,
        ollama_url="http://localhost:11434/api/generate",
        default_model="llama3.2:3b",
        max_chars=30000,
        allow_remote=False,
        conn=conn,
        rebuild=True,
    )
    result = execution.entries_by_variant["default"][0]
    assert result.summary_source == "preview_fallback"
    mock_store.assert_not_called()
    metadata = execution.summary_metadata_by_variant["default"]
    assert metadata.summaries_fallback == 1
    assert len(metadata.anomaly_rows) == 1
    anomaly = metadata.anomaly_rows[0]
    assert anomaly.stop_reason == "local_char_cap"
    assert anomaly.status == "fallback"
    assert anomaly.cached is False
    assert anomaly.output_chars == len(result.summary or "")
