"""Tests for message parsing."""

from __future__ import annotations

from datetime import datetime
from email.message import EmailMessage
from pathlib import Path


from rollup.discovery import iter_mbox_files
from rollup.parse import (
    compute_content_hash,
    compute_message_key,
    normalize_message_id,
    parse_mbox_folder,
    parse_message,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "Newsletters.sbd"


def test_normalize_message_id() -> None:
    assert normalize_message_id("<ABC@example.com>") == "abc@example.com"


def test_message_key_with_message_id() -> None:
    key, warnings = compute_message_key(
        "<id@example.com>", "tech", "Subj", "a@b.c", "Mon", "body"
    )
    assert key.startswith("mid:")
    assert warnings == ()


def test_message_key_fallback() -> None:
    key, warnings = compute_message_key(None, "tech", "Subj", "a@b.c", "", "body")
    assert key.startswith("fb:")
    assert "no_message_id" in warnings


def test_content_hash_stable() -> None:
    h1 = compute_content_hash("hello\n\nworld")
    h2 = compute_content_hash("hello\n\nworld")
    assert h1 == h2


def test_parse_plain_message() -> None:
    msg = EmailMessage()
    msg["Subject"] = "Test"
    msg["From"] = "a@example.com"
    msg["Date"] = datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")
    msg["Message-ID"] = "<plain@example.com>"
    msg.set_content("Hello plain world " * 10)
    parsed = parse_message(msg, "test", "test", 200_000, 8)
    assert "Hello plain" in parsed.body_text
    assert parsed.date_parsed is not None


def test_parse_html_message() -> None:
    msg = EmailMessage()
    msg["Subject"] = "HTML"
    msg["From"] = "b@example.com"
    msg["Message-ID"] = "<html@example.com>"
    msg.add_alternative(
        "<html><body><h1>Title</h1><p>Content</p></body></html>", subtype="html"
    )
    parsed = parse_message(msg, "test", "test", 200_000, 8)
    assert parsed.html_heading_count >= 1
    assert parsed.body_text


def test_parse_missing_date() -> None:
    folders = list(iter_mbox_files(FIXTURE_ROOT))
    misc = next(f for f in folders if f.folder_name == "misc")
    msgs, _, _ = parse_mbox_folder(misc, 200_000, 8)
    undated = [m for m in msgs if m.date_parsed is None]
    assert len(undated) >= 1


def test_body_size_guard() -> None:
    msg = EmailMessage()
    msg["Subject"] = "Big"
    msg["From"] = "a@example.com"
    msg.set_content("x" * 5000)
    parsed = parse_message(msg, "t", "t", max_body_chars=1000, max_display_links=8)
    assert len(parsed.body_text) <= 1000


def test_links_preserve_raw_extracted_duplicates() -> None:
    msg = EmailMessage()
    msg["Subject"] = "Links"
    msg["From"] = "a@example.com"
    html = (
        '<a href="https://example.com/a">a</a> <a href="https://example.com/a">dup</a>'
    )
    msg.add_alternative(f"<html><body>{html}</body></html>", subtype="html")
    parsed = parse_message(msg, "t", "t", 200_000, 8)
    assert len(parsed.links) == 2
    assert len(parsed.link_items) == 2


def test_link_items_preserve_text_and_source_order() -> None:
    msg = EmailMessage()
    msg["Subject"] = "Links"
    msg["From"] = "a@example.com"
    html = (
        '<a href="https://example.com/read">Read article</a>'
        '<a href="https://example.com/register">Register</a>'
    )
    msg.add_alternative(f"<html><body>{html}</body></html>", subtype="html")
    parsed = parse_message(msg, "t", "t", 200_000, 8)
    assert [item.href for item in parsed.link_items] == [
        "https://example.com/read",
        "https://example.com/register",
    ]
    assert parsed.link_items[0].text == "Read article"
    assert parsed.link_items[1].source_index == 1


def test_parse_does_not_truncate_extracted_links() -> None:
    msg = EmailMessage()
    msg["Subject"] = "Many Links"
    msg["From"] = "a@example.com"
    html = (
        "<html><body>"
        + "".join(f'<a href="https://example.com/{i}">Link {i}</a>' for i in range(12))
        + "</body></html>"
    )
    msg.add_alternative(html, subtype="html")
    parsed = parse_message(msg, "t", "t", 200_000, 3)
    assert len(parsed.links) == 12
    assert len(parsed.link_items) == 12


def test_preview_not_empty() -> None:
    folders = list(iter_mbox_files(FIXTURE_ROOT))
    tech = next(f for f in folders if f.folder_name == "tech")
    msgs, _, _ = parse_mbox_folder(tech, 200_000, 8)
    assert msgs[0].preview


def test_read_time_minutes() -> None:
    msg = EmailMessage()
    msg["Subject"] = "Words"
    msg["From"] = "a@example.com"
    msg.set_content(" ".join(["word"] * 400))
    parsed = parse_message(msg, "t", "t", 200_000, 8)
    assert parsed.read_time_minutes >= 1


def test_parse_multipart_alternative() -> None:
    msg = EmailMessage()
    msg["Subject"] = "Alt"
    msg["From"] = "a@example.com"
    msg["Message-ID"] = "<alt@example.com>"
    msg.set_content("short plain")
    msg.add_alternative(
        "<html><body><h1>Title</h1><p>" + ("rich html " * 50) + "</p></body></html>",
        subtype="html",
    )
    parsed = parse_message(msg, "t", "t", 200_000, 8)
    assert len(parsed.body_text) > len("short plain")
    assert parsed.html_heading_count >= 1
    assert parsed.html_link_count >= 0


def test_parse_skips_attachment() -> None:
    msg = EmailMessage()
    msg["Subject"] = "Attach"
    msg["From"] = "a@example.com"
    msg["Message-ID"] = "<attach@example.com>"
    msg.set_content("visible body")
    msg.add_attachment(
        b"secret", maintype="application", subtype="octet-stream", filename="x.bin"
    )
    parsed = parse_message(msg, "t", "t", 200_000, 8)
    assert "visible body" in parsed.body_text
    assert "secret" not in parsed.body_text


def test_parse_bad_charset() -> None:
    msg = EmailMessage()
    msg["Subject"] = "Charset"
    msg["From"] = "a@example.com"
    msg["Message-ID"] = "<charset@example.com>"
    msg.set_payload(b"caf\xe9")
    msg.set_charset("latin-1")
    parsed = parse_message(msg, "t", "t", 200_000, 8)
    assert parsed.body_text


def test_empty_body_hash_and_preview() -> None:
    h1 = compute_content_hash("")
    h2 = compute_content_hash("")
    assert h1 == h2
    msg = EmailMessage()
    msg["Subject"] = "Empty"
    msg["From"] = "a@example.com"
    msg["Message-ID"] = "<empty@example.com>"
    msg.set_content("")
    parsed = parse_message(msg, "t", "t", 200_000, 8)
    assert parsed.preview == ""


def test_content_hash_whitespace_equivalent() -> None:
    assert compute_content_hash("a\n\nb") == compute_content_hash("a\n\n\nb")


def test_content_hash_different_bodies() -> None:
    assert compute_content_hash("hello") != compute_content_hash("hello world")


def test_html_link_count_before_conversion() -> None:
    msg = EmailMessage()
    msg["Subject"] = "Links"
    msg["From"] = "a@example.com"
    msg["Message-ID"] = "<links2@example.com>"
    html = '<a href="https://a.com">1</a><a href="https://b.com">2</a>'
    msg.add_alternative(f"<html><body>{html}</body></html>", subtype="html")
    parsed = parse_message(msg, "t", "t", 200_000, 8)
    assert parsed.html_link_count == 2


def test_non_http_links_ignored_in_link_items() -> None:
    msg = EmailMessage()
    msg["Subject"] = "Mixed links"
    msg["From"] = "a@example.com"
    html = (
        '<a href="mailto:test@example.com">Email</a>'
        '<a href="/relative">Relative</a>'
        '<a href="https://example.com/live">Live</a>'
    )
    msg.add_alternative(f"<html><body>{html}</body></html>", subtype="html")
    parsed = parse_message(msg, "t", "t", 200_000, 8)
    assert parsed.links == ("https://example.com/live",)


def test_parse_extracts_bare_urls_from_html_text_nodes() -> None:
    msg = EmailMessage()
    msg["Subject"] = "Bare HTML URL"
    msg["From"] = "a@example.com"
    html = (
        "<html><body>"
        "<p>Visible URL https://example.com/raw should still be extracted.</p>"
        '<p><a href="https://example.com/linked">Article</a></p>'
        "</body></html>"
    )
    msg.add_alternative(html, subtype="html")
    parsed = parse_message(msg, "t", "t", 200_000, 8)
    assert "https://example.com/raw" in parsed.links
    assert "https://example.com/linked" in parsed.links
    assert len(parsed.link_items) == 2


def test_parse_error_on_first_message_does_not_abort_folder(tmp_path: Path) -> None:
    import mailbox
    from unittest.mock import patch

    from rollup.models import MboxFolder

    mbox_path = tmp_path / "testbox"
    mbox = mailbox.mbox(str(mbox_path))
    good = EmailMessage()
    good["Subject"] = "Good"
    good["From"] = "a@example.com"
    good["Message-ID"] = "<good@example.com>"
    good["Date"] = datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")
    good.set_content("good body")
    mbox.add(good)
    good2 = EmailMessage()
    good2["Subject"] = "Also Good"
    good2["From"] = "b@example.com"
    good2["Message-ID"] = "<good2@example.com>"
    good2["Date"] = datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z")
    good2.set_content("second good body")
    mbox.add(good2)
    mbox.close()

    folder = MboxFolder("test", "test", mbox_path, mbox_path.stat().st_size)
    call_count = 0
    real_parse = parse_message

    def flaky_parse(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("corrupt first message")
        return real_parse(*args, **kwargs)

    with patch("rollup.parse.parse_message", side_effect=flaky_parse):
        msgs, errors, folder_errors = parse_mbox_folder(folder, 200_000, 8)
    assert folder_errors == []
    assert errors == 1
    assert len(msgs) == 1
