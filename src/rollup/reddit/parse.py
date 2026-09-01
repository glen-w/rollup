"""Map RedditPost → ParsedMessage for the digest pipeline."""

from __future__ import annotations

import re

from rollup.models import LinkItem, ParsedMessage
from rollup.parse import compute_content_hash
from rollup.reddit.config import RedditLayout, folder_name_for_sub
from rollup.reddit.models import RedditPost

_SUBJECT_MAX = 280
_PREVIEW_FULL_MAX = 2000


def reddit_message_key(post: RedditPost) -> str:
    safe_id = re.sub(r"[^\w]", "", post.post_id)
    return f"reddit:t3:{safe_id}" if safe_id else f"reddit:t3:unknown"


def reddit_source_key(subreddit: str) -> str:
    safe = re.sub(r"[^\w]", "", subreddit.lower())[:200]
    return f"reddit:sub:{safe}"


def _subject_from_post(post: RedditPost) -> str:
    title = post.title.strip() or "(no title)"
    if len(title) <= _SUBJECT_MAX:
        return title
    return title[: _SUBJECT_MAX - 1] + "…"


def _body_from_post(post: RedditPost) -> str:
    if post.is_self and post.selftext.strip():
        return post.selftext.strip()
    if post.title.strip():
        return post.title.strip()
    return post.url or post.permalink or ""


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


def reddit_post_to_parsed_message(
    post: RedditPost,
    *,
    layout: RedditLayout,
    max_body_chars: int,
    extra_warnings: tuple[str, ...] = (),
) -> ParsedMessage:
    folder_name = folder_name_for_sub(post.subreddit, layout=layout)
    body_text = _body_from_post(post)
    warnings: list[str] = list(extra_warnings)
    if len(body_text) > max_body_chars:
        body_text = body_text[:max_body_chars]
        warnings.append("body_truncated")
    if not body_text:
        warnings.append("empty_body")

    subject = _subject_from_post(post)
    sender = f"u/{post.author}" if post.author else "r/" + post.subreddit
    date_raw = post.created_at.isoformat() if post.created_at else ""
    message_key = reddit_message_key(post)
    link_href = post.permalink or post.url
    link_items: tuple[LinkItem, ...] = ()
    links: tuple[str, ...] = ()
    if link_href:
        link_items = (
            LinkItem(href=link_href, text="View on Reddit", context=None, source_index=0),
        )
        links = (link_href,)
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
        source_key=reddit_source_key(post.subreddit),
        list_id=None,
    )
