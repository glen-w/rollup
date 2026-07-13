"""Deep regression tests for HTML parse / strip / render invariants.

Contract:
1. ``body_text`` is human plaintext (no document-level HTML).
2. ``preview`` / ``preview_fallback`` derive only from ``body_text``.
3. Web reader HTML is always escaped plaintext via ``format_reader_body_html``.
4. Digest summary markdown links only emit http(s) hrefs.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from rollup.classify import classify_message
from rollup.filter import make_digest_entry
from rollup.parse import (
    _choose_body,
    _ensure_plaintext_body,
    _html_to_text,
    _looks_like_html,
    parse_message,
)
from rollup.payload_limits import MAX_READER_BODY_LEN
from rollup.render import _inline_summary_markdown, render_html
from rollup.web.format import format_reader_body_html, reader_body_fragment_html


def _assert_plaintext(text: str) -> None:
    lowered = text.lstrip().lower()
    assert not lowered.startswith(("<!doctype", "<html", "<head", "<body"))
    assert "<script" not in lowered
    assert "</html>" not in lowered


# --- detector ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("<!doctype html><html><body><p>Hi</p></body></html>", True),
        ("\r\n\r\n<!DOCTYPE HTML><HTML><BODY><P>Hi</P></BODY></HTML>", True),
        ("\ufeff<!DOCTYPE html><html><body><p>Hi</p></body></html>", True),
        ("<!--[if mso]><xml></xml><![endif]--><div><p>Hi</p></div>", True),
        (
            "<div><table><tr><td>Cell</td></tr></table><p>More</p></div>",
            True,
        ),
        (
            '<a href="https://a.example/1">one</a> '
            '<a href="https://a.example/2">two</a> '
            '<a href="https://a.example/3">three</a> '
            '<a href="https://a.example/4">four</a>',
            True,
        ),
        ("Just a normal newsletter paragraph about heatwaves.", False),
        ("Use <3 hearts and code like `x < y` carefully.", False),
        ("See the <p> tag docs sometime.", False),
    ],
)
def test_looks_like_html_matrix(text: str, expected: bool) -> None:
    assert _looks_like_html(text) is expected


def test_looks_like_html_dense_obfuscated_markup() -> None:
    # Unusual tags still trip density gate.
    blob = "".join(f"<x{i}>pad</x{i}>" for i in range(12))
    assert _looks_like_html(blob)


# --- conversion / choose_body -----------------------------------------------


def test_html_to_text_strips_script_style_and_iframe() -> None:
    html = (
        "<html><body>"
        "<script>secret_token()</script>"
        "<style>.x{color:red}</style>"
        "<noscript>noscript noise</noscript>"
        '<iframe src="https://evil.example"></iframe>'
        "<p>Visible tortoise content</p>"
        "</body></html>"
    )
    out = _html_to_text(html)
    assert "Visible tortoise content" in out
    assert "secret_token" not in out
    assert "noscript noise" not in out
    assert "<script" not in out.lower()
    assert "evil.example" not in out


def test_html_to_text_nested_tables_and_outlook_conditionals() -> None:
    html = (
        "<!DOCTYPE html><html><body>"
        "<!--[if mso]><p>Outlook only</p><![endif]-->"
        "<table><tr><td><table><tr><td>"
        "<h1>Heatwave</h1><p>A thirsty tortoise.</p>"
        "</td></tr></table></td></tr></table>"
        "</body></html>"
    )
    out = _html_to_text(html)
    _assert_plaintext(out)
    assert "thirsty tortoise" in out.lower()
    assert "Heatwave" in out


def test_choose_body_discards_longer_html_plain_dump() -> None:
    real = _html_to_text(
        "<html><body><h1>Title</h1><p>Readable prose about turtles.</p></body></html>"
    )
    plain_dump = (
        "<!doctype html><html><body><h1>Title</h1>"
        + ("<p>pad</p>" * 80)
        + "</body></html>"
    )
    assert len(plain_dump) > len(real) * 1.25
    chosen = _choose_body(plain_dump, real)
    assert chosen == real
    _assert_plaintext(chosen)
    assert "<table" not in chosen.lower()
    assert "</html>" not in chosen.lower()


def test_choose_body_converts_html_only_plain() -> None:
    plain = "<!doctype html><html><body><p>Solo HTML plain part.</p></body></html>"
    chosen = _choose_body(plain, "")
    _assert_plaintext(chosen)
    assert "Solo HTML plain part" in chosen


def test_ensure_plaintext_body_converts_residual_html() -> None:
    raw = "<html><body><p>Residual dump</p></body></html>"
    assert "Residual dump" in _ensure_plaintext_body(raw)
    _assert_plaintext(_ensure_plaintext_body(raw))
    assert _ensure_plaintext_body("Already fine text") == "Already fine text"


# --- parse_message end-to-end -----------------------------------------------


def test_parse_html_only_message_strips_markup() -> None:
    msg = EmailMessage()
    msg["Subject"] = "Nested"
    msg["From"] = "a@example.com"
    msg["Message-ID"] = "<nested-html@example.com>"
    msg.add_alternative(
        "<html><body><script>bad()</script>"
        "<table><tr><td><p>Deep cell copy</p></td></tr></table>"
        "</body></html>",
        subtype="html",
    )
    parsed = parse_message(msg, "tech", "tech", 200_000, 8)
    _assert_plaintext(parsed.body_text)
    assert "Deep cell copy" in parsed.body_text
    assert "bad()" not in parsed.body_text
    # Raw MIME HTML is retained separately and must not equal body_text.
    assert parsed.body_html is not None
    assert parsed.body_html != parsed.body_text
    assert "<table" in parsed.body_html.lower()


def test_parse_uppercase_crlf_html_plain_dump() -> None:
    dump = (
        "\r\n\r\n<!DOCTYPE HTML><HTML><BODY>"
        "<H1>HEATWAVE</H1><P>A thirsty tortoise.</P>"
        "</BODY></HTML>"
    )
    real = (
        "<!DOCTYPE html><html><body>"
        "<h1>Heatwave</h1><p>A thirsty tortoise.</p></body></html>"
    )
    msg = EmailMessage()
    msg["Subject"] = "Upper"
    msg["From"] = "a@example.com"
    msg["Message-ID"] = "<upper-html@example.com>"
    msg.set_content(dump + ("<!--x-->" * 100))
    msg.add_alternative(real, subtype="html")
    parsed = parse_message(msg, "brainfood", "brainfood", 200_000, 8)
    _assert_plaintext(parsed.body_text)
    assert "thirsty tortoise" in parsed.body_text.lower()


def test_parse_bom_prefixed_html_as_plain() -> None:
    html_doc = "\ufeff<!DOCTYPE html><html><body><p>BOM body</p></body></html>"
    msg = EmailMessage()
    msg["Subject"] = "BOM"
    msg["From"] = "a@example.com"
    msg["Message-ID"] = "<bom-html@example.com>"
    msg.set_content(html_doc)
    parsed = parse_message(msg, "misc", "misc", 200_000, 8)
    _assert_plaintext(parsed.body_text)
    assert "BOM body" in parsed.body_text


def test_parse_anchor_heavy_plain_dump_prefers_converted_html() -> None:
    anchors = " ".join(
        f'<a href="https://example.com/{i}">link {i}</a><br>' for i in range(20)
    )
    plain = f"<div>{anchors}<p>Buried prose in dump</p></div>"
    real = (
        "<html><body><p>Clean readable article about climate.</p>"
        '<a href="https://example.com/story">Read</a></body></html>'
    )
    msg = EmailMessage()
    msg["Subject"] = "Anchors"
    msg["From"] = "a@example.com"
    msg["Message-ID"] = "<anchor-dump@example.com>"
    msg.set_content(plain)
    msg.add_alternative(real, subtype="html")
    parsed = parse_message(msg, "tech", "tech", 200_000, 8)
    _assert_plaintext(parsed.body_text)
    assert "Clean readable article" in parsed.body_text
    assert parsed.body_text.count("<a ") == 0


def test_preview_fallback_does_not_carry_raw_doctype() -> None:
    dump = (
        "<!doctype html><html><body><h1>Week notes</h1>"
        "<p>Devolution and a thirsty tortoise.</p></body></html>"
    )
    real = (
        "<html><body><h1>Week notes</h1>"
        "<p>Devolution and a thirsty tortoise.</p></body></html>"
    )
    msg = EmailMessage()
    msg["Subject"] = "Preview"
    msg["From"] = "a@example.com"
    msg["Message-ID"] = "<preview-html@example.com>"
    msg.set_content(dump + ("<!--pad-->" * 50))
    msg.add_alternative(real, subtype="html")
    parsed = parse_message(msg, "brainfood", "brainfood", 200_000, 8)
    entry = make_digest_entry(classify_message(parsed), no_ollama=True)
    assert entry.summary_source == "preview_fallback"
    assert entry.summary is not None
    _assert_plaintext(entry.summary)
    assert "thirsty tortoise" in entry.summary.lower()
    _assert_plaintext(parsed.preview)


# --- web reader formatting --------------------------------------------------


def test_format_reader_body_html_escapes_tags() -> None:
    text = "Hello <script>alert(1)</script> & friends <b>bold</b>"
    html = format_reader_body_html(text)
    assert "<script>" not in html
    assert "<b>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;" in html
    assert "&amp;" in html
    assert 'data-reader-body-fragment' in html


def test_format_reader_body_html_rejects_javascript_markdown_link() -> None:
    text = "See [x](javascript:alert(1)) and [ok](https://example.com/a)"
    html = format_reader_body_html(text)
    assert 'href="javascript:' not in html.lower()
    assert 'href="https://example.com/a"' in html
    assert ">ok</a>" in html


def test_format_reader_body_html_rejects_data_and_protocol_relative() -> None:
    text = "[a](data:text/html,hi) [b](//evil.example/x) [c](https://ok.example)"
    html = format_reader_body_html(text)
    assert 'href="data:' not in html
    assert 'href="//evil' not in html
    assert 'href="https://ok.example"' in html


def test_format_reader_body_html_oversized_falls_back_to_pre() -> None:
    # Many bare URLs expand into long <a> tags and trip the HTML budget.
    text = " ".join(f"https://example.com/path/{i}" for i in range(5000))
    html = format_reader_body_html(text)
    assert '<pre class="reader-plain">' in html
    assert "https://example.com/path/0" in html


def test_reader_body_truncate_mid_markup_is_escaped() -> None:
    # Simulate a clipped HTML-looking body (legacy bad data).
    raw = "<div style='color:red'>" + ("x" * (MAX_READER_BODY_LEN - 30))
    clipped = raw[:MAX_READER_BODY_LEN]
    html = reader_body_fragment_html(clipped, truncated=True)
    assert "Body truncated" in html
    assert "<div style" not in html
    assert "&lt;div" in html


def test_prepare_path_keeps_literal_tags_for_escaping_layer() -> None:
    from rollup.reader_bodies import prepare_reader_text

    prepared = prepare_reader_text("<b>bold</b>\r\n\r\nnext")
    assert "<b>bold</b>" in prepared.text
    html = format_reader_body_html(prepared.text)
    assert "&lt;b&gt;bold&lt;/b&gt;" in html


# --- digest render ----------------------------------------------------------


def test_digest_summary_rejects_javascript_markdown_href() -> None:
    from datetime import datetime

    from rollup.config import compute_date_window
    from rollup.models import DigestReport, DigestStats

    msg = EmailMessage()
    msg["Subject"] = "JS link"
    msg["From"] = "a@example.com"
    msg["Message-ID"] = "<js-link@example.com>"
    msg.set_content("safe body text for classification")
    parsed = parse_message(msg, "tech", "tech", 200_000, 8)
    entry = make_digest_entry(
        classify_message(parsed),
        no_ollama=True,
        summary="See [Click](javascript:alert(1)) and [Ok](https://example.com/ok)",
        summary_source="preview_fallback",
    )
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)
    report = DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"tech": (entry,)},
        undated=(),
        stats=DigestStats(
            folders_scanned=1,
            messages_parsed=1,
            dated_included=1,
            undated_needing_review=0,
            skipped_outside_window=0,
            skipped_seen_undated=0,
            deduped_messages=0,
            parse_errors=0,
            summaries_ollama=0,
            summaries_cache=0,
            summaries_fallback=1,
        ),
    )
    html = render_html(report, 8)
    assert "javascript:" not in html.lower()
    assert 'href="https://example.com/ok"' in html
    assert "Click" in html


def test_inline_summary_markdown_sanitizes_hrefs() -> None:
    # Mimic escaped summary text fed into the rewriter.
    import html as html_module

    escaped = html_module.escape(
        "[bad](javascript:alert(1)) [also](data:text/html,x) [ok](https://ex.com/a?x=1&y=2)"
    )
    out = _inline_summary_markdown(escaped)
    assert "javascript:" not in out.lower()
    assert "data:" not in out.lower()
    assert 'href="https://ex.com/a?x=1&amp;y=2"' in out
