"""Tests for link sanitisation."""

from __future__ import annotations

import pytest

from rollup.links_sanitize import (
    LinkSanitizeError,
    build_links_json,
    parse_links_json,
    parse_unsubscribe_link,
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


def test_links_json_unsubscribe_roundtrip():
    raw = build_links_json(
        [("https://a.example/", "A")],
        unsubscribe="https://a.example/unsubscribe",
    )
    assert parse_unsubscribe_link(raw) == "https://a.example/unsubscribe"
    assert parse_links_json(raw)[0]["href"] == "https://a.example/"
    validated = validate_links_json_for_index(raw)
    assert parse_unsubscribe_link(validated) == "https://a.example/unsubscribe"


def test_links_json_rejects_bad_unsubscribe():
    with pytest.raises(LinkSanitizeError):
        validate_links_json_for_index(
            '{"v":1,"items":[],"unsubscribe":"javascript:alert(1)"}'
        )


def test_validate_rejects_bad_version():
    with pytest.raises(LinkSanitizeError):
        validate_links_json_for_index('{"v":99,"items":[]}')
