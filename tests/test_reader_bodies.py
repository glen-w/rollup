"""Tests for reader body core types and hashing."""

from __future__ import annotations

import pytest

from rollup.parse import compute_content_hash
from rollup.reader_bodies import (
    ReaderBodyError,
    compute_stored_body_hash,
    make_reader_body_write,
    prepare_reader_text,
)
from rollup.payload_limits import MAX_READER_BODY_LEN


def test_make_reader_body_write_clips():
    source = "x" * (MAX_READER_BODY_LEN + 10)
    w = make_reader_body_write("mid:a@b.c", compute_content_hash(source), source)
    assert len(w.body_text) == MAX_READER_BODY_LEN
    assert w.truncated is True


def test_empty_body_allowed():
    h = compute_content_hash("")
    w = make_reader_body_write("mid:a@b.c", h, "")
    assert w.body_text == ""
    assert w.truncated is False


def test_nul_rejected():
    h = compute_content_hash("x")
    with pytest.raises(ReaderBodyError):
        make_reader_body_write("mid:a@b.c", h, "hello\x00world")


def test_stored_hash_deterministic():
    h1 = compute_stored_body_hash(truncated=False, body_text="hi")
    h2 = compute_stored_body_hash(truncated=False, body_text="hi")
    assert h1 == h2
    assert len(h1) == 64


def test_prepare_reader_text_idempotent():
    raw = "Line one\r\n\r\n\r\n\r\nLine two"
    p1 = prepare_reader_text(raw)
    p2 = prepare_reader_text(p1.text)
    assert p1.text == p2.text


def test_prepare_reader_text_preserves_substance():
    text = "Important content with https://example.com/path"
    p = prepare_reader_text(text)
    assert "Important content" in p.text
    assert "https://example.com/path" in p.text
