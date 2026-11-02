"""Parser robustness against a generated corpus of malformed mbox input.

The contract distinguishes discovered / parse-candidate / parsed-ok /
parse-failed. Whatever is in the mbox, ``iter_parsed_messages`` must yield
``(message, None)`` or ``(None, error)`` and never raise, and a digest over a
tree containing such a folder must still publish the healthy folders.
"""

from __future__ import annotations

import os
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

import pytest

from rollup.parse import iter_parsed_messages
from rollup.pipeline import EXIT_FAILURE, EXIT_PARTIAL, EXIT_SUCCESS
from test_hardening_contracts import NOW, _config, _digest_files, _run

FROM_LINE = "From - Sun Jul 12 12:00:00 2026\n"


def _msg(subject: str, body: str, *, date: datetime | None = NOW, **headers) -> str:
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = "Sender <sender@example.com>"
    m["To"] = "reader@example.com"
    if date is not None:
        m["Date"] = format_datetime(date)
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    return m.as_string()


def _truncated_headers() -> str:
    return "Subject: cut off mid\nFrom: who@exa"


def _garbage_date() -> str:
    return _msg("Bad date", "body", date=None, Date="31st of Febtober, 2026 25:61")


def _year_9999_date() -> str:
    return _msg("Far future", "body", date=None, Date="Fri, 31 Dec 9999 23:59:59 +0000")


def _negative_tz_overflow_date() -> str:
    return _msg("Weird tz", "body", date=None, Date="Mon, 01 Jan 2026 00:00:00 +9999")


def _crlf() -> str:
    return _msg("CRLF message", "line one\nline two\n").replace("\n", "\r\n")


def _invalid_utf8_body() -> bytes:
    head = (
        "Subject: Latin bytes\nFrom: s@example.com\nTo: r@example.com\n"
        f"Date: {format_datetime(NOW)}\nContent-Type: text/plain; charset=utf-8\n\n"
    ).encode()
    return head + b"caf\xe9 \xff\xfe broken \x80 bytes\n"


def _unescaped_from_in_body() -> str:
    return _msg("From in body", "quoted mail:\nFrom here it looks like a new message\nbut it is not\n")


def _deep_multipart(depth: int = 6) -> str:
    inner = EmailMessage()
    inner.set_content("leaf text")
    for level in range(depth):
        outer = EmailMessage()
        outer.make_mixed()
        outer.attach(inner)
        part = EmailMessage()
        part.set_content(f"sibling {level}")
        outer.attach(part)
        inner = outer
    inner["Subject"] = "Deep multipart"
    inner["From"] = "s@example.com"
    inner["To"] = "r@example.com"
    inner["Date"] = format_datetime(NOW)
    return inner.as_string()


def _oversized_body(chars: int) -> str:
    return _msg("Oversized", "x" * chars + "\n")


def _binary_attachment_only() -> str:
    m = EmailMessage()
    m["Subject"] = "Attachment only"
    m["From"] = "s@example.com"
    m["To"] = "r@example.com"
    m["Date"] = format_datetime(NOW)
    m.make_mixed()
    m.add_attachment(os.urandom(2048), maintype="application", subtype="octet-stream", filename="blob.bin")
    return m.as_string()


def _header_only() -> str:
    return "Subject: no body at all\nFrom: s@example.com\nTo: r@example.com\n\n"


def _html_only_broken() -> str:
    m = EmailMessage()
    m["Subject"] = "Broken HTML"
    m["From"] = "s@example.com"
    m["To"] = "r@example.com"
    m["Date"] = format_datetime(NOW)
    m.set_content("<html><body><div><p>unclosed <b>tags <a href='http://x'>link", subtype="html")
    return m.as_string()


def _huge_subject() -> str:
    return _msg("S" * 20_000, "body")


def _many_links(n: int = 3000) -> str:
    return _msg("Link storm", "\n".join(f"https://example.com/{i}" for i in range(n)))


def _duplicate_message_ids() -> list[str]:
    return [
        _msg("Dup A", "body a", Message_ID="<dup@example.com>"),
        _msg("Dup B", "body b", Message_ID="<dup@example.com>"),
    ]


def _write_mbox(path: Path, chunks: list[str | bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        for chunk in chunks:
            fh.write(FROM_LINE.encode("utf-8"))
            fh.write(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
            if not (chunk.endswith(b"\n") if isinstance(chunk, bytes) else chunk.endswith("\n")):
                fh.write(b"\n")


def _corpus(max_body_chars: int) -> list[str | bytes]:
    return [
        _msg("Healthy control", "A normal message body that should parse."),
        _truncated_headers(),
        _garbage_date(),
        _year_9999_date(),
        _negative_tz_overflow_date(),
        _crlf(),
        _invalid_utf8_body(),
        _unescaped_from_in_body(),
        _deep_multipart(),
        _oversized_body(max_body_chars * 3),
        _binary_attachment_only(),
        _header_only(),
        _html_only_broken(),
        _huge_subject(),
        _many_links(),
        *_duplicate_message_ids(),
    ]


def test_iter_parsed_messages_never_raises_on_corpus(tmp_path: Path) -> None:
    mbox = tmp_path / "Newsletters.sbd" / "weird"
    _write_mbox(mbox, _corpus(max_body_chars=5_000))

    results = list(
        iter_parsed_messages(mbox, "weird", "weird", max_body_chars=5_000, max_display_links=8)
    )
    assert results, "mbox splitting produced nothing"
    for parsed, err in results:
        assert (parsed is None) != (err is None), (parsed, err)
    ok = [p for p, _ in results if p is not None]
    assert any(p.subject == "Healthy control" for p in ok)
    # Oversize bodies are capped, never passed through whole.
    for p in ok:
        assert len(p.body_text) <= 5_000 + 64
        assert len(p.links) <= 3000


@pytest.mark.parametrize(
    "raw",
    [b"", b"\x00" * 512, os.urandom(4096), b"From - no message follows\n", b"\xff\xfe" * 100],
)
def test_non_mbox_bytes_do_not_raise(tmp_path: Path, raw: bytes) -> None:
    mbox = tmp_path / "Newsletters.sbd" / "junk"
    mbox.parent.mkdir(parents=True)
    mbox.write_bytes(raw)
    results = list(iter_parsed_messages(mbox, "junk", "junk", 10_000, 8))
    for parsed, err in results:
        assert (parsed is None) != (err is None)


def test_missing_mbox_path_yields_error_not_exception(tmp_path: Path) -> None:
    results = list(iter_parsed_messages(tmp_path / "nope", "nope", "nope", 10_000, 8))
    assert results == [] or all(p is None and e for p, e in results)


def test_digest_survives_malformed_folder_and_publishes_healthy_ones(tmp_path: Path) -> None:
    from test_hardening_contracts import _mail_tree

    root = _mail_tree(tmp_path)  # healthy 'tech'
    _write_mbox(root / "weird", _corpus(max_body_chars=5_000))
    (root / "junk").write_bytes(os.urandom(2048))
    (root / "empty").write_bytes(b"")

    config = _config(tmp_path, root=root, max_body_chars=5_000)
    result = _run(config)

    assert result.exit_code in (EXIT_SUCCESS, EXIT_PARTIAL), result.error_message
    assert result.exit_code != EXIT_FAILURE
    assert _digest_files(config.output_dir, "md")
    md = _digest_files(config.output_dir, "md")[0].read_text(encoding="utf-8")
    assert "Dated hardening newsletter" in md
    assert "Healthy control" in md
    assert result.stats is not None
    assert result.stats.messages_parsed >= 2
    # Nothing from the corpus escaped as raw bytes into the rendered output.
    assert "\ufffd" not in md or "Latin bytes" in md


def test_zero_byte_folder_alone_is_empty_window_not_crash(tmp_path: Path) -> None:
    root = tmp_path / "mail" / "Newsletters.sbd"
    root.mkdir(parents=True)
    (root / "empty").write_bytes(b"")
    config = _config(tmp_path, root=root)
    result = _run(config)
    # Readable store with no mail is an empty window (0) or a named no-input failure (1),
    # never an unhandled exception.
    assert result.exit_code in (EXIT_SUCCESS, EXIT_FAILURE)
    assert result.error_message is None or "Traceback" not in result.error_message


def test_undated_and_badly_dated_messages_land_in_undated_section(tmp_path: Path) -> None:
    root = tmp_path / "mail" / "Newsletters.sbd"
    _write_mbox(root / "dates", [_garbage_date(), _year_9999_date(), _msg("No date", "b", date=None)])
    config = _config(tmp_path, root=root)
    result = _run(config)
    assert result.exit_code in (EXIT_SUCCESS, EXIT_PARTIAL)
    assert result.stats is not None
    assert result.stats.undated_needing_review >= 2
