"""Payload clipping and constant sanity checks."""

from __future__ import annotations

from rollup import payload_limits as pl


def test_clip_text_none_and_short() -> None:
    assert pl.clip_text(None, 10) is None
    assert pl.clip_text("hello", 10) == "hello"
    assert pl.clip_text("hello", 5) == "hello"


def test_clip_text_truncates_without_ellipsis() -> None:
    assert pl.clip_text("abcdefghij", 5) == "abcde"
    assert pl.clip_text(12345, 3) == "123"  # type: ignore[arg-type]


def test_page_size_bounds_are_ordered() -> None:
    assert 0 < pl.DEFAULT_PAGE_SIZE <= pl.MAX_PAGE_SIZE
    assert pl.MAX_READER_BODY_LEN > 0
    assert pl.MAX_LINK_ITEMS > 0
    assert pl.SQL_IN_CHUNK_SIZE > 0
