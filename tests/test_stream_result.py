"""Offline contracts for provider-neutral stream stop reasons."""

from __future__ import annotations

from rollup.stream_result import (
    NON_CACHEABLE_STOP_REASONS,
    StreamResult,
    is_stop_reason_cacheable,
)


def test_truncated_property_for_length_and_local_caps() -> None:
    for reason in ("local_char_cap", "local_wall_timeout", "provider_length"):
        result = StreamResult(
            text="x",
            stop_reason=reason,  # type: ignore[arg-type]
            output_chars=1,
            eval_count=None,
            elapsed_seconds=0.1,
        )
        assert result.truncated is True


def test_truncated_false_for_done_and_errors() -> None:
    for reason in ("done", "parse_error", "http_error", "eof_without_done"):
        result = StreamResult(
            text="",
            stop_reason=reason,  # type: ignore[arg-type]
            output_chars=0,
            eval_count=None,
            elapsed_seconds=0.0,
        )
        assert result.truncated is False


def test_cacheable_stop_reasons() -> None:
    assert is_stop_reason_cacheable("done") is True
    assert is_stop_reason_cacheable("provider_length") is True
    for reason in NON_CACHEABLE_STOP_REASONS:
        assert is_stop_reason_cacheable(reason) is False
