"""Tests for shared Ollama stream consumer."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from rollup.ollama_stream import consume_ollama_stream


def _stream_lines(*chunks: dict[str, object]):
    resp = MagicMock()
    resp.iter_lines.return_value = [
        json.dumps(chunk) for chunk in chunks
    ]
    return resp


def test_stops_at_local_char_cap() -> None:
    resp = _stream_lines(
        {"response": "a" * 300, "done": False},
        {"response": "b" * 300, "done": False},
    )
    result = consume_ollama_stream(
        resp, max_output_chars=400, max_wall_seconds=30.0, show_progress=False
    )
    assert result.stop_reason == "local_char_cap"
    assert result.output_chars == 400
    resp.close.assert_called_once()


def test_stops_at_wall_timeout() -> None:
    resp = MagicMock()
    resp.iter_lines.return_value = iter(
        [json.dumps({"response": "x", "done": False})] * 100
    )
    times = iter([0.0, 0.0, 3.0])
    with patch("rollup.ollama_stream.monotonic", side_effect=lambda: next(times)):
        result = consume_ollama_stream(
            resp,
            max_output_chars=10_000,
            max_wall_seconds=2.0,
            show_progress=False,
        )
    assert result.stop_reason == "local_wall_timeout"
    resp.close.assert_called_once()


def test_eof_without_done() -> None:
    resp = _stream_lines({"response": "partial", "done": False})
    result = consume_ollama_stream(
        resp, max_output_chars=1000, show_progress=False
    )
    assert result.stop_reason == "eof_without_done"
    assert result.text == "partial"
    resp.close.assert_called_once()


def test_parse_error() -> None:
    resp = MagicMock()
    resp.iter_lines.return_value = ["not-json"]
    result = consume_ollama_stream(
        resp, max_output_chars=1000, show_progress=False
    )
    assert result.stop_reason == "parse_error"
    resp.close.assert_called_once()


def test_http_error_from_stream_payload() -> None:
    resp = _stream_lines({"error": "model failed"})
    result = consume_ollama_stream(
        resp, max_output_chars=1000, show_progress=False
    )
    assert result.stop_reason == "http_error"
    resp.close.assert_called_once()


def test_done_normal() -> None:
    resp = _stream_lines(
        {"response": "Hello ", "done": False},
        {"response": "world", "done": True, "eval_count": 3},
    )
    result = consume_ollama_stream(
        resp, max_output_chars=1000, show_progress=False
    )
    assert result.stop_reason == "done"
    assert result.text == "Hello world"
    assert result.eval_count == 3
    resp.close.assert_not_called()


def test_provider_length_done_reason() -> None:
    resp = _stream_lines(
        {"response": "truncated", "done": True, "done_reason": "length"}
    )
    result = consume_ollama_stream(
        resp, max_output_chars=1000, show_progress=False
    )
    assert result.stop_reason == "provider_length"


def test_progress_throttled_by_chars_and_time(capsys) -> None:
    resp = _stream_lines(
        *[{"response": "x", "done": False} for _ in range(600)],
        {"response": "", "done": True, "eval_count": 1},
    )
    tick = iter([0.0] + [float(i) for i in range(1, 2000)])
    with patch("rollup.ollama_stream.monotonic", side_effect=lambda: next(tick)):
        consume_ollama_stream(
            resp,
            max_output_chars=10_000,
            show_progress=True,
            progress_interval_chars=50,
            progress_interval_seconds=2.0,
        )
    err = capsys.readouterr().err
    assert err.count("generating") < 600


def test_quiet_suppresses_progress(capsys) -> None:
    resp = _stream_lines(
        {"response": "abc", "done": True, "eval_count": 1},
    )
    consume_ollama_stream(resp, max_output_chars=1000, show_progress=False)
    assert capsys.readouterr().err == ""


def test_response_close_on_early_stop() -> None:
    cases = [
        _stream_lines({"response": "a" * 500, "done": False}),
        _stream_lines({"response": "partial", "done": False}),
        MagicMock(iter_lines=MagicMock(return_value=["{bad json"])),
        _stream_lines({"error": "boom"}),
    ]
    expected = ["local_char_cap", "eof_without_done", "parse_error", "http_error"]
    for resp, reason in zip(cases, expected):
        if reason == "local_char_cap":
            result = consume_ollama_stream(
                resp, max_output_chars=100, show_progress=False
            )
        else:
            result = consume_ollama_stream(
                resp, max_output_chars=1000, show_progress=False
            )
        assert result.stop_reason == reason
        resp.close.assert_called_once()
