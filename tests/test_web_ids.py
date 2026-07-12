"""Tests for web ID encoding and validation."""

from __future__ import annotations

import pytest

from rollup.web_ids import (
    IdError,
    decode_opaque,
    encode_opaque,
    validate_message_key,
    validate_run_id,
    validate_source_key,
)


def test_opaque_roundtrip_message():
    key = "mid:abc123@example.com"
    token = encode_opaque(key)
    assert "/" not in token
    assert decode_opaque(token, kind="message") == key


def test_opaque_roundtrip_source():
    key = "list:news.example.com"
    assert decode_opaque(encode_opaque(key), kind="source") == key


def test_reject_path_chars():
    with pytest.raises(IdError):
        validate_message_key("mid:foo/bar")
    with pytest.raises(IdError):
        validate_source_key("from:a@b.com/../x")


def test_run_id_uuid():
    rid = "550e8400-e29b-41d4-a716-446655440000"
    assert validate_run_id(rid) == rid
    with pytest.raises(IdError):
        validate_run_id("not-a-uuid")


def test_opaque_length_limit():
    with pytest.raises(IdError):
        decode_opaque("a" * 2000, kind="message")
