"""Map fetched webpage content to ParsedMessage."""

from __future__ import annotations

from datetime import datetime

from rollup.models import LinkItem, ParsedMessage
from rollup.parse import compute_content_hash
from rollup.webpage.config import WEBPAGE_FOLDER_NAME
from rollup.webpage.url import message_key_for_url, source_key_for_url

_SUBJECT_MAX = 280
_PREVIEW_FULL_MAX = 2000


def _subject(title: str | None, url: str, body: str) -> str:
    if title and title.strip():
        cleaned = title.strip()
        if len(cleaned) <= _SUBJECT_MAX:
            return cleaned
        return cleaned[: _SUBJECT_MAX - 1] + "…"
    cleaned = " ".join(body.split())
    if cleaned:
        first = cleaned.split("\n", 1)[0].strip()
        if first:
            if len(first) <= _SUBJECT_MAX:
                return first
            return first[: _SUBJECT_MAX - 1] + "…"
    return url


def _make_preview(body_text: str) -> str:
    cleaned = body_text.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= _PREVIEW_FULL_MAX:
        return cleaned
    return cleaned[: _PREVIEW_FULL_MAX - 1] + "…"


def _read_time_minutes(body_text: str) -> int:
    words = len(body_text.split())
    return max(1, (words + 199) // 200)


def webpage_to_parsed_message(
    *,
    url: str,
    title: str | None,
    body_text: str,
    saved_at: datetime,
    display_title: str | None = None,
    max_body_chars: int,
    extra_warnings: tuple[str, ...] = (),
) -> ParsedMessage:
    subject_title = display_title or title
    body = body_text.strip()
    warnings: list[str] = list(extra_warnings)
    if len(body) > max_body_chars:
        body = body[:max_body_chars]
        warnings.append("body_truncated")
    if not body:
        warnings.append("empty_body")

    subject = _subject(subject_title, url, body)
    message_key = message_key_for_url(url)
    link_items = (
        LinkItem(href=url, text="Open article", context=None, source_index=0),
    )
    preview = _make_preview(body)

    return ParsedMessage(
        message_key=message_key,
        content_hash=compute_content_hash(body),
        folder_name=WEBPAGE_FOLDER_NAME,
        relative_folder_path=WEBPAGE_FOLDER_NAME,
        subject=subject,
        sender=url,
        date_raw=saved_at.isoformat(),
        date_parsed=saved_at,
        body_text=body,
        body_html=None,
        html_heading_count=0,
        html_link_count=1,
        html_section_break_count=0,
        links=(url,),
        link_items=link_items,
        read_time_minutes=_read_time_minutes(body),
        preview=preview,
        parse_warnings=tuple(warnings),
        source_key=source_key_for_url(url),
        list_id=None,
    )
