"""Human-friendly date formatting and reader body HTML for the web UI."""

from __future__ import annotations

import html
import re
from datetime import datetime
from email.utils import parseaddr

from rollup.payload_limits import MAX_LINK_HREF_LEN, MAX_READER_HTML_CHARS
from rollup.links_sanitize import sanitize_http_url
from rollup.render import (
    _folder_accent_class,
    _folder_display_name,
    _folder_section_id,
)

_BARE_URL = re.compile(r"https?://", re.IGNORECASE)
_STOP_CHARS = set(' \t\r\n<>"\'')
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_TRAILING_PUNCT = ".,;:!?)]}"


def _parse_iso(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def format_human_datetime(value: str | datetime | None) -> str:
    """Format a point-in-time, e.g. Sunday 12 July 2026, 23:57."""
    dt = _parse_iso(value)
    if dt is None:
        return "—" if value in (None, "") else str(value)
    return f"{dt.strftime('%A')} {dt.day} {dt.strftime('%B %Y, %H:%M')}"


def format_human_date_range(
    start: str | datetime | None,
    end: str | datetime | None,
) -> str:
    """Format a date window, e.g. Monday 29 June – Sunday 12 July, 2026."""
    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    if start_dt is None or end_dt is None:
        if start and end:
            return f"{start} → {end}"
        return "—"

    start_label = f"{start_dt.strftime('%A')} {start_dt.day} {start_dt.strftime('%B')}"
    end_label = f"{end_dt.strftime('%A')} {end_dt.day} {end_dt.strftime('%B')}"

    if start_dt.date() == end_dt.date():
        return f"{start_label}, {start_dt.year}"

    if start_dt.year == end_dt.year and start_dt.month == end_dt.month:
        return (
            f"{start_dt.strftime('%A')} {start_dt.day} – "
            f"{end_dt.strftime('%A')} {end_dt.day} "
            f"{end_dt.strftime('%B')}, {end_dt.year}"
        )

    if start_dt.year == end_dt.year:
        return f"{start_label} – {end_label}, {end_dt.year}"

    return (
        f"{start_label} {start_dt.year} – {end_label}, {end_dt.year}"
    )


def external_link_attrs() -> str:
    """Shared anchor attributes for external http(s) links."""
    return 'rel="noopener noreferrer"'


def format_display_sender(sender: str | None) -> str:
    """Human-readable sender name without the email address."""
    name, addr = parseaddr(sender or "")
    display = name.strip()
    if display:
        return display
    if addr:
        return addr.split("@", 1)[0]
    return (sender or "").strip() or "—"


def format_newsletter_type(ntype: str | None) -> str:
    if not ntype:
        return ""
    parts = ntype.split("_")
    if len(parts) == 1:
        return parts[0].capitalize()
    return f"{parts[0].capitalize()} {' '.join(parts[1:])}"


def folder_display_name(folder: str | None) -> str:
    return _folder_display_name(folder or "misc")


def folder_accent_class(folder: str | None) -> str:
    return _folder_accent_class(folder or "misc")


def folder_section_id(folder: str | None) -> str:
    return _folder_section_id(folder or "misc")


def _escape_plain(text: str) -> str:
    return html.escape(text, quote=True)


def _strip_trailing_punct(url: str) -> str:
    while url and url[-1] in _TRAILING_PUNCT:
        if url[-1] == ")" and url.count("(") < url.count(")"):
            break
        url = url[:-1]
    return url


def _parse_balanced_url(text: str, start: int, max_paren_depth: int = 3) -> tuple[str, int] | None:
    if start >= len(text) or not text[start : start + 8].lower().startswith(("http://", "https://")):
        return None
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch in _STOP_CHARS or _CONTROL.match(ch):
            break
        if ch == "(":
            depth += 1
            if depth > max_paren_depth:
                break
        elif ch == ")":
            depth -= 1
        i += 1
    raw = text[start:i]
    raw = _strip_trailing_punct(raw)
    if len(raw) > MAX_LINK_HREF_LEN:
        return None
    safe = sanitize_http_url(raw)
    if safe is None:
        return None
    return safe, i


def _try_markdown_link(text: str, pos: int) -> tuple[str, int] | None:
    if pos >= len(text) or text[pos] != "[":
        return None
    close = text.find("]", pos + 1)
    if close < 0:
        return None
    label = text[pos + 1 : close]
    if "[" in label or "]" in label:
        return None
    if close + 1 >= len(text) or text[close + 1] != "(":
        return None
    url_start = close + 2
    depth = 0
    i = url_start
    while i < len(text):
        ch = text[i]
        if ch == ")":
            if depth == 0:
                break
            depth -= 1
        elif ch == "(":
            depth += 1
            if depth > 3:
                return None
        i += 1
    else:
        return None
    raw_url = text[url_start:i]
    if len(raw_url) > MAX_LINK_HREF_LEN:
        return None
    safe = sanitize_http_url(raw_url.strip())
    end = i + 1
    if safe is None:
        return _escape_plain(text[pos:end]), end
    href = html.escape(safe, quote=True)
    lab = _escape_plain(label)
    return f'<a href="{href}" {external_link_attrs()}>{lab}</a>', end


def format_reader_body_html(text: str) -> str:
    """Tokenise plaintext into safe HTML with links."""
    if not text:
        return ""
    parts: list[str] = []
    pos = 0
    while pos < len(text):
        md = _try_markdown_link(text, pos)
        if md is not None:
            parts.append(md[0])
            pos = md[1]
            continue
        if _BARE_URL.match(text, pos):
            parsed = _parse_balanced_url(text, pos)
            if parsed is not None:
                safe, end = parsed
                href = html.escape(safe, quote=True)
                parts.append(f'<a href="{href}" {external_link_attrs()}>{href}</a>')
                pos = end
                continue
        parts.append(_escape_plain(text[pos]))
        pos += 1
    result = "".join(parts)
    if len(result) > MAX_READER_HTML_CHARS:
        return f'<pre class="reader-plain">{_escape_plain(text)}</pre>'
    return f'<div class="reader-body-text" data-reader-body-fragment>{result}</div>'


def reader_body_fragment_html(text: str, *, truncated: bool) -> str:
    """Fragment with optional truncation notice."""
    if not text.strip():
        return (
            '<div class="reader-body-text" data-reader-body-fragment>'
            "<p>No plaintext body was available for this newsletter.</p></div>"
        )
    notice = ""
    if truncated:
        notice = '<p class="reader-notice">Body truncated at 32,000 characters.</p>'
    return notice + format_reader_body_html(text)
