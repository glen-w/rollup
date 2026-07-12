"""Tests for link sanitisation."""

from __future__ import annotations

import pytest

from rollup.links_sanitize import (
    LinkSanitizeError,
    build_links_json,
    parse_links_json,
    sanitize_http_url,
    validate_links_json_for_index,
)


def test_sanitize_rejects_javascript_and_data():
    assert sanitize_http_url("javascript:alert(1)") is None
    assert sanitize_http_url("data:text/html,hi") is None
    assert sanitize_http_url("//evil.example/x") is None
    assert sanitize_http_url("https://ok.example/a") == "https://ok.example/a"


def test_links_json_roundtrip():
    raw = build_links_json([("https://a.example/", "A"), ("javascript:x", "bad")])
    items = parse_links_json(raw)
    assert len(items) == 1
    assert items[0]["href"] == "https://a.example/"
    validate_links_json_for_index(raw)


def test_validate_rejects_bad_version():
    with pytest.raises(LinkSanitizeError):
        validate_links_json_for_index('{"v":99,"items":[]}')
