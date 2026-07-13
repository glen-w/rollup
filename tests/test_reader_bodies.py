"""Tests for reader body core types and hashing."""

from __future__ import annotations

import pytest

from rollup.parse import compute_content_hash
from rollup.reader_bodies import (
    READER_TEXT_VERSION,
    ReaderBodyError,
    compute_stored_body_hash,
    make_reader_body_write,
    normalize_plaintext_layout,
    prepare_reader_text,
    validate_reader_body_write,
)
from rollup.payload_limits import MAX_READER_BODY_LEN


def test_make_reader_body_write_clips():
    source = "x" * (MAX_READER_BODY_LEN + 10)
    w = make_reader_body_write("mid:a@b.c", compute_content_hash(source), source)
    assert len(w.body_text) == MAX_READER_BODY_LEN
    assert w.truncated is True


def test_validate_reader_body_write_accepts_truncated():
    source = "x" * (MAX_READER_BODY_LEN + 10)
    w = make_reader_body_write("mid:a@b.c", compute_content_hash(source), source)
    validate_reader_body_write(w)


def test_validate_reader_body_write_rejects_bad_hash():
    source = "hello"
    w = make_reader_body_write("mid:a@b.c", compute_content_hash(source), source)
    bad = type(w)(
        message_key=w.message_key,
        content_hash=w.content_hash,
        body_text=w.body_text,
        truncated=w.truncated,
        stored_body_hash="0" * 64,
    )
    with pytest.raises(ReaderBodyError, match="invariant mismatch"):
        validate_reader_body_write(bad)


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


def test_prepare_reader_text_version_is_current():
    p = prepare_reader_text("hello")
    assert p.reader_text_version == READER_TEXT_VERSION
    assert READER_TEXT_VERSION >= 2


def test_normalize_plaintext_layout_strips_substack_chrome():
    raw = (
        "Jul 7| | | •| | Paid  \n"
        "---|---|---|---|---|---  \n"
        "\n"
        "* * *\n"
        "\n"
        "| | [READ IN APP](https://example.com/app)  \n"
        "---|---|---  \n"
        "|   \n"
        "---|---  \n"
        "[Subscribed](https://example.com/sub)\n"
        "\n"
        "Real article about a thirsty tortoise.\n"
    )
    clean = normalize_plaintext_layout(raw)
    assert "|" not in clean
    assert "---" not in clean
    assert "* * *" not in clean
    assert "Jul 7" in clean and "•" in clean and "Paid" in clean
    assert "[READ IN APP](https://example.com/app)" in clean
    assert "[Subscribed](https://example.com/sub)" in clean
    assert "thirsty tortoise" in clean
    assert clean == normalize_plaintext_layout(clean)


@pytest.mark.parametrize(
    "raw,expected_fragment",
    [
        ("Use the command: cat file | grep foo", "cat file | grep foo"),
        ("Final score was 3|2 after overtime.", "3|2"),
        ("She wrote a|b once.", "a|b"),
    ],
)
def test_normalize_preserves_single_pipe_prose(raw: str, expected_fragment: str) -> None:
    assert expected_fragment in normalize_plaintext_layout(raw)


def test_normalize_flattens_leading_pipe_rows_only():
    raw = "| Alpha | Beta |\n| --- | --- |\n| 1 | 2 |"
    clean = normalize_plaintext_layout(raw)
    assert clean == "Alpha Beta\n1 2"


def test_normalize_collapses_blank_runs_and_multi_space():
    raw = "Hello    world\n\n\n\nNext"
    clean = normalize_plaintext_layout(raw)
    assert clean == "Hello world\n\nNext"


def test_prepare_reader_text_drops_empty_image_placeholders():
    raw = "Before\n![]\n![]( )\n![]()\nAfter"
    p = prepare_reader_text(raw)
    assert p.text == "Before\nAfter"
