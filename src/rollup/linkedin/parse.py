"""Map LinkedInPost → ParsedMessage for the digest pipeline."""

from __future__ import annotations

import hashlib
import re

from rollup.linkedin.config import linkedin_folder_for_post
from rollup.linkedin.models import LinkedInPost
from rollup.models import LinkItem, ParsedMessage
from rollup.parse import compute_content_hash, compute_message_key

_SUBJECT_MAX = 280
_PREVIEW_FULL_MAX = 2000


def _subject_from_text(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return "(no text)"
    first_line = cleaned.split("\n", 1)[0].strip()
    if len(first_line) <= _SUBJECT_MAX:
        return first_line
    return first_line[: _SUBJECT_MAX - 1] + "…"


def _subject_from_post(post: LinkedInPost) -> str:
    if post.article_title:
        title = post.article_title.strip()
        if title:
            if len(title) <= _SUBJECT_MAX:
                return title
            return title[: _SUBJECT_MAX - 1] + "…"
    return _subject_from_text(post.text)


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


def linkedin_message_key(post: LinkedInPost) -> tuple[str, tuple[str, ...]]:
    if post.activity_id:
        safe_id = re.sub(r"[^\w:.-]", "", post.activity_id)
        if safe_id:
            return f"li:activity:{safe_id}", ()
    payload = "\0".join(
        [
            post.author_name,
            post.text[:4096],
            post.permalink,
            post.created_at.isoformat() if post.created_at else "",
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()
    return f"li:fb:{digest}", ("no_activity_id",)


def linkedin_source_key(post: LinkedInPost) -> str | None:
    if post.author_member_id:
        safe = re.sub(r"[^\w]", "", post.author_member_id)
        if safe:
            return f"li:member:{safe[:200]}"
    return None


def linkedin_post_to_parsed_message(
    post: LinkedInPost,
    *,
    search_slug: str,
    max_body_chars: int,
    layout: str = "feed",
    extra_warnings: tuple[str, ...] = (),
) -> ParsedMessage:
    folder_name = linkedin_folder_for_post(
        search_slug=search_slug,
        layout=layout,
        author_member_id=post.author_member_id,
    )
    body_text = post.text.strip()
    warnings: list[str] = list(extra_warnings)
    if len(body_text) > max_body_chars:
        body_text = body_text[:max_body_chars]
        warnings.append("body_truncated")
    if not body_text:
        warnings.append("empty_body")

    subject = _subject_from_post(post)
    sender = post.author_name or "(unknown)"
    date_raw = post.created_at.isoformat() if post.created_at else ""
    message_key, key_warnings = linkedin_message_key(post)
    warnings.extend(key_warnings)
    link_items: tuple[LinkItem, ...] = ()
    links: tuple[str, ...] = ()
    if post.permalink:
        link_items = (LinkItem(href=post.permalink, text="View on LinkedIn", context=None, source_index=0),)
        links = (post.permalink,)
    preview = _make_preview(body_text)

    return ParsedMessage(
        message_key=message_key,
        content_hash=compute_content_hash(body_text),
        folder_name=folder_name,
        relative_folder_path=folder_name,
        subject=subject,
        sender=sender,
        date_raw=date_raw,
        date_parsed=post.created_at,
        body_text=body_text,
        body_html=None,
        html_heading_count=0,
        html_link_count=len(links),
        html_section_break_count=0,
        links=links,
        link_items=link_items,
        read_time_minutes=_read_time_minutes(body_text),
        preview=preview,
        parse_warnings=tuple(warnings),
        source_key=linkedin_source_key(post),
        list_id=None,
    )
