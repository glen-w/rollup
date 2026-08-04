"""Shared offline-friendly text helpers for link-free digest writers."""

from __future__ import annotations

import re

# Optimal line length for e-ink / plain-text digests.
OFFLINE_LINE_LENGTH = 60

# Keep labels; drop destinations — offline digests are readable without the network.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", re.IGNORECASE)
_BARE_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_WWW_URL_RE = re.compile(r"\bwww\.\S+", re.IGNORECASE)


def strip_urls_for_offline(text: str) -> str:
    """Remove markdown and bare URLs from text for offline display."""
    if not text:
        return ""
    text = _MD_LINK_RE.sub(r"\1", text)
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
