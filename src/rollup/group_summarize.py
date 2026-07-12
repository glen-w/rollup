"""Opt-in group-level summary stage.

Generates a synthesised group summary for eligible DigestGroups (notification_stream
and daily_editions) using a local Ollama model.  Results are cached in the state DB.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Sequence

from rollup.config import Config
from rollup.models import (
    DigestEntry,
    DigestGroup,
    DigestItem,
    GroupSummaryMetadata,
)
from rollup.summarize import clean_summary_output

logger = logging.getLogger(__name__)

GROUP_SUMMARY_PROMPT_VERSION = 1
GROUPING_VERSION = "grouping_v1"

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_GROUP_SUMMARY_PROMPT_PATH = PROMPTS_DIR / "group_summary.txt"

_ELIGIBLE_GROUP_TYPES = frozenset({"notification_stream", "daily_editions"})

# Absolute caps applied when formatting prompt members.
_MAX_MEMBER_SUMMARY_CHARS = 400
_MAX_PROMPT_CHARS_DEFAULT = 12_000


def _load_group_summary_prompt() -> str:
    return _GROUP_SUMMARY_PROMPT_PATH.read_text(encoding="utf-8")


def _is_summary_usable_simple(summary: str | None) -> bool:
    if not summary:
        return False
    cleaned = clean_summary_output(summary)
    return bool(cleaned.strip())


def _format_group_prompt(group: DigestGroup, *, max_input_chars: int) -> str:
    """Build a deterministic prompt for the group."""
    base_prompt = _load_group_summary_prompt()
    lines: list[str] = []
    char_budget = max_input_chars - len(base_prompt) - 200

    for i, entry in enumerate(group.entries, 1):
        parsed = entry.classified.parsed
        date_str = (
            parsed.date_parsed.strftime("%Y-%m-%d") if parsed.date_parsed else "unknown"
        )
        title = (parsed.subject or "").strip()[:120]
        summary = entry.summary or ""
        capped = summary[:_MAX_MEMBER_SUMMARY_CHARS]
        if len(summary) > _MAX_MEMBER_SUMMARY_CHARS:
            capped += "…"
        member_block = (
            f"[{i}] id={parsed.message_key!r} date={date_str}\n"
            f"    title: {title}\n"
            f"    summary: {capped}"
        )
        if char_budget <= 0:
            break
        lines.append(member_block)
        char_budget -= len(member_block)

    members_text = "\n\n".join(lines)
    return f"{base_prompt}\n\nGroup: {group.display_name!r}\nMembers:\n\n{members_text}"


def _group_cache_key(group: DigestGroup, config: Config) -> str:
    """Stable cache key for a group summary generation."""
    parts = [
        GROUPING_VERSION,
        str(GROUP_SUMMARY_PROMPT_VERSION),
        group.group_id,
        config.ollama_model,
        str(config.ollama_url),
    ]
    for entry in group.entries:
        parts.append(entry.classified.parsed.message_key)
        parts.append(entry.classified.parsed.content_hash)
    blob = "\n".join(parts).encode()
    return hashlib.sha256(blob).hexdigest()


def _count_usable_summaries(group: DigestGroup) -> int:
    return sum(1 for e in group.entries if _is_summary_usable_simple(e.summary))


def _is_eligible(
    group: DigestGroup,
    *,
    min_group_size: int,
    min_usable: int,
) -> bool:
    if group.group_type not in _ELIGIBLE_GROUP_TYPES:
        return False
    if len(group.entries) < min_group_size:
        return False
    if _count_usable_summaries(group) < min_usable:
        return False
    return True


def _call_ollama_for_group(prompt: str, config: Config) -> str | None:
    """Call Ollama and return the raw text response, or None on failure."""
    try:
        from rollup.ollama_stream import consume_ollama_stream
        import urllib.request

        payload = json.dumps(
            {
                "model": config.ollama_model,
                "prompt": prompt,
                "stream": True,
                "options": {"temperature": 0.1},
            }
        ).encode()
        req = urllib.request.Request(
            config.ollama_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw_bytes = resp.read()
        lines = raw_bytes.decode(errors="replace").splitlines()
        chunks: list[str] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            response_chunk = obj.get("response", "")
            if response_chunk:
                chunks.append(response_chunk)
            if obj.get("done"):
                break
        return "".join(chunks) if chunks else None
    except Exception as exc:
        logger.debug("group_summarize ollama call failed: %s", exc)
        return None


def _get_group_summary_generation(
    conn: sqlite3.Connection,
    cache_key: str,
) -> str | None:
    """Look up a cached group summary by cache_key.  Returns None on miss."""
    try:
        from rollup.state import get_group_summary_generation

        return get_group_summary_generation(conn, cache_key=cache_key)
    except (AttributeError, Exception) as exc:
        logger.debug("get_group_summary_generation unavailable: %s", exc)
        return None


def _store_group_summary_generation(
    conn: sqlite3.Connection,
    cache_key: str,
    summary: str,
    created_at: datetime,
) -> None:
    """Persist a group summary to the cache.  No-ops gracefully if not available."""
    try:
        from rollup.state import store_group_summary_generation

        store_group_summary_generation(
            conn,
            cache_key=cache_key,
            summary=summary,
            created_at=created_at,
        )
    except (AttributeError, Exception) as exc:
        logger.debug("store_group_summary_generation unavailable: %s", exc)


def _summarise_group(
    group: DigestGroup,
    config: Config,
    conn: sqlite3.Connection | None,
    *,
    max_input_chars: int,
    stats: dict[str, int],
) -> str | None:
    """Return a group summary string, using cache when possible."""
    cache_key = _group_cache_key(group, config)

    if conn is not None:
        cached = _get_group_summary_generation(conn, cache_key)
        if cached is not None:
            stats["cache_hits"] += 1
            logger.debug("group_summarize cache hit for %r", group.group_id)
            return cached

    prompt = _format_group_prompt(group, max_input_chars=max_input_chars)
    stats["ollama_calls"] += 1
    raw = _call_ollama_for_group(prompt, config)
    if raw is None:
        stats["errors"] += 1
        return None

    cleaned = clean_summary_output(raw)
    if not cleaned.strip():
        stats["errors"] += 1
        return None

    if conn is not None:
        _store_group_summary_generation(conn, cache_key, cleaned, datetime.now())

    return cleaned


def _apply_group_summaries_to_items(
    items: tuple[DigestItem, ...],
    config: Config,
    conn: sqlite3.Connection | None,
    *,
    min_group_size: int,
    min_usable: int,
    max_calls: int,
    max_input_chars: int,
    stats: dict[str, int],
) -> tuple[DigestItem, ...]:
    result: list[DigestItem] = []
    for item in items:
        if not isinstance(item, DigestGroup):
            result.append(item)
            continue

        if not _is_eligible(item, min_group_size=min_group_size, min_usable=min_usable):
            result.append(item)
            continue

        if stats["groups_attempted"] >= max_calls:
            stats["groups_skipped_budget"] += 1
            result.append(item)
            continue

        stats["groups_attempted"] += 1
        summary = _summarise_group(
            item, config, conn, max_input_chars=max_input_chars, stats=stats
        )
        if summary is not None:
            stats["groups_succeeded"] += 1
            item = replace(item, group_summary=summary, group_summary_source="ollama")
        else:
            stats["groups_failed"] += 1

        result.append(item)

    return tuple(result)


def apply_group_summaries(
    dated_items: dict[str, tuple[DigestItem, ...]],
    undated_items: tuple[DigestItem, ...],
    config: Config,
    conn: sqlite3.Connection | None,
    *,
    max_calls: int = 8,
    max_input_chars: int = _MAX_PROMPT_CHARS_DEFAULT,
) -> tuple[dict[str, tuple[DigestItem, ...]], tuple[DigestItem, ...], GroupSummaryMetadata]:
    """Apply group summaries to eligible groups across dated and undated items.

    Returns updated (dated_items, undated_items, GroupSummaryMetadata).
    """
    min_group_size: int = getattr(config, "grouping_min_group_size", 3)
    # Prefer explicit grouping min when attached; fall back to 3.
    min_usable: int = getattr(config, "min_usable_member_summaries", 2)

    stats: dict[str, int] = {
        "groups_attempted": 0,
        "groups_succeeded": 0,
        "groups_failed": 0,
        "groups_skipped_budget": 0,
        "ollama_calls": 0,
        "cache_hits": 0,
        "fallback_count": 0,
        "errors": 0,
    }

    new_dated: dict[str, tuple[DigestItem, ...]] = {}
    for folder, items in dated_items.items():
        new_dated[folder] = _apply_group_summaries_to_items(
            items,
            config,
            conn,
            min_group_size=min_group_size,
            min_usable=min_usable,
            max_calls=max_calls,
            max_input_chars=max_input_chars,
            stats=stats,
        )

    new_undated = _apply_group_summaries_to_items(
        undated_items,
        config,
        conn,
        min_group_size=min_group_size,
        min_usable=min_usable,
        max_calls=max_calls,
        max_input_chars=max_input_chars,
        stats=stats,
    )

    metadata = GroupSummaryMetadata(
        groups_attempted=stats["groups_attempted"],
        groups_succeeded=stats["groups_succeeded"],
        groups_failed=stats["groups_failed"],
        groups_skipped_budget=stats["groups_skipped_budget"],
        ollama_calls=stats["ollama_calls"],
        cache_hits=stats["cache_hits"],
        fallback_count=stats["fallback_count"],
        errors=stats["errors"],
    )
    return new_dated, new_undated, metadata
