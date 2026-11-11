"""Offline unit tests for provider error redaction."""

from __future__ import annotations

from rollup.error_sanitize import sanitize_provider_message


def test_sanitize_openai_style_key() -> None:
    msg = "upstream failed: sk-abcdefghijklmnopqrstuvwxyz1234"
    assert "[REDACTED]" in sanitize_provider_message(msg)
    assert "sk-abcdefgh" not in sanitize_provider_message(msg)


def test_sanitize_bearer_token() -> None:
    out = sanitize_provider_message("Authorization Bearer abc.def.ghi_jkl")
    assert "[REDACTED]" in out
    assert "abc.def" not in out


def test_sanitize_api_key_assignment() -> None:
    out = sanitize_provider_message('api_key="supersecretvalue123"')
    assert "[REDACTED]" in out
    assert "supersecret" not in out


def test_sanitize_linkedin_cookie_names() -> None:
    out = sanitize_provider_message("li_at=AQFVeryLongTokenValue12345; path=/")
    assert "[REDACTED]" in out
    assert "AQFVery" not in out
    out2 = sanitize_provider_message("JSESSIONID=ajax1234567890abcdef")
    assert "[REDACTED]" in out2
    assert "ajax1234" not in out2


def test_sanitize_cookie_header() -> None:
    out = sanitize_provider_message("Cookie: session=abc123def456; other=1")
    assert "[REDACTED]" in out
    assert "session=abc" not in out


def test_sanitize_leaves_benign_message() -> None:
    msg = "Ollama returned 500: model not found"
    assert sanitize_provider_message(msg) == msg
