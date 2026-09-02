"""Shared offline-friendly text helpers for link-free digest writers."""

from __future__ import annotations

import re

# Optimal line length for e-ink / plain-text digests.
OFFLINE_LINE_LENGTH = 60

# Compact grouped items (notification streams, daily editions).
COMPACT_SUBJECT_MAX = 100
COMPACT_PREVIEW_MAX = 200
# HTML notification-stream previews stay slightly tighter.
COMPACT_HTML_STREAM_PREVIEW_MAX = 120

# Subreddit digest: titles and 3–5 bullet summaries are the reading product.
# 1000 chars covers a complete short summary; clips runaway essays.
SUBREDDIT_SUBJECT_MAX = 280
SUBREDDIT_SUMMARY_MAX = 1000

# Keep labels; drop destinations — offline digests are readable without the network.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", re.IGNORECASE)
_ANGLE_URL_RE = re.compile(r"<https?://[^>\s]+>", re.IGNORECASE)
_BARE_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_WWW_URL_RE = re.compile(r"\bwww\.\S+", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"[.!?]")


def strip_urls_for_offline(text: str) -> str:
    """Remove markdown and bare URLs from text for offline display."""
    if not text:
        return ""
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _ANGLE_URL_RE.sub("", text)
    text = _BARE_URL_RE.sub("", text)
    text = _WWW_URL_RE.sub("", text)
    # Collapse leftover whitespace from removals without destroying newlines.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def wrap_text(text: str, line_length: int = OFFLINE_LINE_LENGTH) -> str:
    """Wrap text to the given line length for narrow-display readability."""
    if not text:
        return ""

    lines = text.split("\n")
    wrapped_lines: list[str] = []

    for line in lines:
        if len(line) <= line_length:
            wrapped_lines.append(line)
            continue
        words = line.split()
        current_line = ""
        for word in words:
            if len(current_line) + len(word) + 1 <= line_length:
                if current_line:
                    current_line += " " + word
                else:
                    current_line = word
            else:
                if current_line:
                    wrapped_lines.append(current_line)
                current_line = word
        if current_line:
            wrapped_lines.append(current_line)

    return "\n".join(wrapped_lines)


def truncate_with_ellipsis(text: str, max_chars: int) -> str:
    """Truncate at a word boundary when possible; append an ellipsis."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0].rstrip()
    return cut + "…"


def clip_heading(text: str, max_chars: int) -> str:
    """Cap a heading, preferring a complete sentence, then a word boundary."""
    text = " ".join((text or "").split())
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    best_end = -1
    for match in _SENTENCE_END_RE.finditer(window):
        end = match.end()
        if end < 40:
            continue
        if end < len(window) and window[end].isalpha():
            continue
        best_end = end
    if best_end >= 40:
        return window[:best_end].rstrip()
    return truncate_with_ellipsis(text, max_chars)


def truncate_summary(text: str, max_chars: int) -> str:
    """Truncate a summary preferring a complete line/bullet, then a word."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    last_nl = window.rfind("\n")
    if last_nl >= 40:
        cut = window[:last_nl].rstrip()
        if cut:
            return cut + "…"
    return truncate_with_ellipsis(text, max_chars)


def indent_multiline(text: str, prefix: str = "   ") -> str:
    """Indent each line so a summary can nest under a Markdown list item."""
    if not text:
        return ""
    return "\n".join((prefix + line) if line else "" for line in text.splitlines())
