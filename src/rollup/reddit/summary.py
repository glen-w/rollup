"""Deterministic subreddit digest blurbs (no LLM required)."""

from __future__ import annotations

from rollup.models import DigestEntry


def deterministic_subreddit_blurb(entries: list[DigestEntry], *, max_items: int = 8) -> str:
    lines: list[str] = []
    for entry in entries[:max_items]:
        parsed = entry.classified.parsed
        title = parsed.subject.strip() or "(no title)"
        if len(title) > 80:
            title = title[:79] + "…"
        lines.append(f"- {title}")
    remaining = len(entries) - max_items
    if remaining > 0:
        lines.append(f"- …and {remaining} more post{'s' if remaining != 1 else ''}")
    return "\n".join(lines)
