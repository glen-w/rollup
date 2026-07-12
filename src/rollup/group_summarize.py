"""Opt-in group-level summary stage.

Generates a synthesised group summary for eligible DigestGroups
(notification_stream and daily_editions) using a local Ollama model.
Results are cached in the flat ``group_summary_by_key`` table (schema v6).

Stream / budget contracts:
  - Uses ``consume_ollama_stream`` with group-specific ``GROUP_SUMMARY_MAX_OUTPUT_CHARS``
    and wall timeout from ``GROUP_SUMMARY_TIMEOUT_SECONDS``.
  - ``ollama_calls`` increments immediately before each HTTP attempt (including
    failed attempts and retries). Cache hits and ineligible groups do not count.
  - Each retry consumes the call budget (``max_group_summary_calls`` bounds network work).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from time import perf_counter

from rollup.config import Config
from rollup.final_review_codes import GroupSummaryErrorCode
from rollup.provider_errors import is_provider_call_error
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

# Group-specific stream limits (not entry-summary defaults).
GROUP_SUMMARY_MAX_OUTPUT_CHARS = 1_200
GROUP_SUMMARY_TIMEOUT_SECONDS = 90.0
GROUP_SUMMARY_HTTP_TIMEOUT = 120
GROUP_SUMMARY_MAX_RETRIES = 1  # total attempts = 1 + retries

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_GROUP_SUMMARY_PROMPT_PATH = PROMPTS_DIR / "group_summary.txt"

_ELIGIBLE_GROUP_TYPES = frozenset({"notification_stream", "daily_editions"})

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


def _map_stream_stop_to_error(stop_reason: str) -> GroupSummaryErrorCode:
    if stop_reason in ("local_char_cap", "provider_length"):
        return "response_oversized"
    if stop_reason == "local_wall_timeout":
        return "stream_timeout"
    if stop_reason in ("parse_error", "eof_without_done"):
        return "stream_malformed"
    if stop_reason == "http_error":
        return "ollama_http_error"
    return "stream_truncated"


def _call_ollama_for_group(
    prompt: str,
    config: Config,
    *,
    stats: dict,
) -> tuple[str | None, GroupSummaryErrorCode | None]:
    """One HTTP attempt. Caller increments ollama_calls before invoking."""
    import requests

    from rollup.ollama_stream import consume_ollama_stream, is_stop_reason_cacheable

    payload = {
        "model": config.ollama_model,
        "prompt": prompt,
        "stream": True,
        "options": {"temperature": 0.1},
    }
    started = perf_counter()
    try:
        resp = requests.post(
            config.ollama_url,
            json=payload,
            timeout=GROUP_SUMMARY_HTTP_TIMEOUT,
            stream=True,
        )
        resp.raise_for_status()
    except Exception as exc:
        if not is_provider_call_error(exc):
            raise
        logger.warning("group_summarize HTTP error: %s", exc)
        stats["stream_failures"] += 1
        return None, "ollama_http_error"

    try:
        stream_result = consume_ollama_stream(
            resp,
            max_output_chars=GROUP_SUMMARY_MAX_OUTPUT_CHARS,
            max_wall_seconds=GROUP_SUMMARY_TIMEOUT_SECONDS,
            show_progress=False,
            started_at=started,
        )
    except Exception as exc:
        if not is_provider_call_error(exc):
            raise
        logger.warning("group_summarize stream consumer failed: %s", exc)
        stats["stream_failures"] += 1
        return None, "stream_malformed"

    if not is_stop_reason_cacheable(stream_result.stop_reason):
        code = _map_stream_stop_to_error(stream_result.stop_reason)
        stats["stream_failures"] += 1
        logger.warning(
            "group_summarize stream stop_reason=%s mapped=%s",
            stream_result.stop_reason,
            code,
        )
        return None, code

    text = stream_result.text.strip()
    if not text:
        stats["stream_failures"] += 1
        return None, "stream_malformed"
    return text, None


def _get_cached(
    conn: sqlite3.Connection | None,
    cache_key: str,
    stats: dict,
) -> str | None:
    if conn is None:
        return None
    try:
        from rollup.state import get_group_summary_generation

        cached = get_group_summary_generation(conn, cache_key=cache_key)
    except sqlite3.OperationalError as exc:
        # Missing table → treat as absent schema / miss
        if "no such table" in str(exc).lower():
            stats["error_counts"]["cache_schema_absent"] += 1
            logger.warning("group_summarize cache schema absent: %s", exc)
            return None
        stats["errors"] += 1
        stats["error_counts"]["cache_read_error"] += 1
        stats["degraded"] = True
        logger.warning("group_summarize cache read error: %s", exc)
        return None
    except sqlite3.Error as exc:
        stats["errors"] += 1
        stats["error_counts"]["cache_read_error"] += 1
        stats["degraded"] = True
        logger.warning("group_summarize cache read error: %s", exc)
        return None

    if cached is None:
        return None
    if not isinstance(cached, str) or not cached.strip():
        stats["error_counts"]["cache_read_corrupt"] += 1
        logger.warning("group_summarize cache corrupt for key %s…", cache_key[:12])
        return None
    return cached


def _store_cached(
    conn: sqlite3.Connection | None,
    cache_key: str,
    summary: str,
    stats: dict,
) -> None:
    if conn is None:
        return
    try:
        from rollup.state import store_group_summary_generation

        store_group_summary_generation(
            conn,
            cache_key=cache_key,
            summary=summary,
            created_at=datetime.now().astimezone(),
        )
    except sqlite3.Error as exc:
        stats["errors"] += 1
        stats["cache_write_errors"] += 1
        stats["error_counts"]["cache_write_error"] += 1
        stats["degraded"] = True
        logger.warning(
            "group_summarize cache write failed (summary still used): %s", exc
        )


def _summarise_group(
    group: DigestGroup,
    config: Config,
    conn: sqlite3.Connection | None,
    *,
    max_input_chars: int,
    max_calls: int,
    stats: dict,
) -> str | None:
    cache_key = _group_cache_key(group, config)

    cached = _get_cached(conn, cache_key, stats)
    if cached is not None:
        stats["cache_hits"] += 1
        logger.debug("group_summarize cache hit for %r", group.group_id)
        return cached

    prompt = _format_group_prompt(group, max_input_chars=max_input_chars)
    last_error: GroupSummaryErrorCode | None = None
    attempts = 1 + GROUP_SUMMARY_MAX_RETRIES

    for attempt in range(attempts):
        if stats["ollama_calls"] >= max_calls:
            stats["error_counts"]["budget_skipped"] += 1
            stats["groups_skipped_budget"] += 1
            return None

        # Increment immediately before each HTTP attempt (including retries).
        stats["ollama_calls"] += 1
        raw, err = _call_ollama_for_group(prompt, config, stats=stats)
        if raw is not None:
            cleaned = clean_summary_output(raw)
            if cleaned.strip():
                _store_cached(conn, cache_key, cleaned, stats)
                return cleaned
            last_error = "stream_malformed"
            stats["stream_failures"] += 1
        else:
            last_error = err

    stats["errors"] += 1
    if last_error:
        stats["error_counts"][last_error] += 1
    stats["error_counts"]["retry_exhausted"] += 1
    stats["degraded"] = True
    return None


def _apply_group_summaries_to_items(
    items: tuple[DigestItem, ...],
    config: Config,
    conn: sqlite3.Connection | None,
    *,
    min_group_size: int,
    min_usable: int,
    max_calls: int,
    max_input_chars: int,
    stats: dict,
) -> tuple[DigestItem, ...]:
    """Preserve input order; never reorder folders/groups/members."""
    result: list[DigestItem] = []
    for item in items:
        if not isinstance(item, DigestGroup):
            result.append(item)
            continue

        if not _is_eligible(item, min_group_size=min_group_size, min_usable=min_usable):
            stats["error_counts"]["ineligible"] += 1
            result.append(item)
            continue

        if stats["ollama_calls"] >= max_calls:
            # Still allow cache hits without burning budget.
            cache_key = _group_cache_key(item, config)
            cached = _get_cached(conn, cache_key, stats)
            if cached is not None:
                stats["cache_hits"] += 1
                stats["groups_attempted"] += 1
                stats["groups_succeeded"] += 1
                result.append(
                    replace(item, group_summary=cached, group_summary_source="cache")
                )
                continue
            stats["groups_skipped_budget"] += 1
            stats["error_counts"]["budget_skipped"] += 1
            result.append(item)
            continue

        stats["groups_attempted"] += 1
        summary = _summarise_group(
            item,
            config,
            conn,
            max_input_chars=max_input_chars,
            max_calls=max_calls,
            stats=stats,
        )
        if summary is not None:
            stats["groups_succeeded"] += 1
            item = replace(item, group_summary=summary, group_summary_source="ollama")
        else:
            stats["groups_failed"] += 1
            stats["degraded"] = True

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
    """Apply group summaries to eligible groups. Preserves folder/group order."""
    min_group_size: int = getattr(config, "grouping_min_group_size", 3)
    min_usable: int = getattr(config, "min_usable_member_summaries", 2)

    stats: dict = {
        "groups_attempted": 0,
        "groups_succeeded": 0,
        "groups_failed": 0,
        "groups_skipped_budget": 0,
        "ollama_calls": 0,
        "cache_hits": 0,
        "errors": 0,
        "degraded": False,
        "cache_write_errors": 0,
        "stream_failures": 0,
        "error_counts": Counter(),
    }

    # Preserve insertion order of dated folder keys.
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

    if stats["groups_attempted"] > 0 and stats["groups_succeeded"] == 0:
        stats["degraded"] = True

    error_counts = stats["error_counts"]
    metadata = GroupSummaryMetadata(
        groups_attempted=stats["groups_attempted"],
        groups_succeeded=stats["groups_succeeded"],
        groups_failed=stats["groups_failed"],
        groups_skipped_budget=stats["groups_skipped_budget"],
        ollama_calls=stats["ollama_calls"],
        cache_hits=stats["cache_hits"],
        errors=stats["errors"],
        degraded=bool(stats["degraded"]),
        error_counts=tuple(sorted(error_counts.items())),
        cache_write_errors=stats["cache_write_errors"],
        stream_failures=stats["stream_failures"],
    )
    return new_dated, new_undated, metadata
