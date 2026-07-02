"""Weekly filtering, deduplication, and digest entry building."""

from __future__ import annotations

from datetime import datetime

from rollup.classify import classify_message
from rollup.config import compute_date_window
from rollup.models import (
    ClassifiedMessage,
    DigestEntry,
    DigestStats,
    ParsedMessage,
    SummarySource,
)


def dedupe_messages(messages: list[ParsedMessage]) -> tuple[list[ParsedMessage], int]:
    """Deduplicate by message_key. Returns (deduped, count_removed)."""
    by_key: dict[str, ParsedMessage] = {}
    for msg in messages:
        existing = by_key.get(msg.message_key)
        if existing is None:
            by_key[msg.message_key] = msg
            continue
        if _should_replace(existing, msg):
            by_key[msg.message_key] = msg
    deduped = list(by_key.values())
    return deduped, len(messages) - len(deduped)


def _should_replace(existing: ParsedMessage, candidate: ParsedMessage) -> bool:
    if candidate.date_parsed and not existing.date_parsed:
        return True
    if existing.date_parsed and candidate.date_parsed:
        if candidate.date_parsed > existing.date_parsed:
            return True
        if candidate.date_parsed < existing.date_parsed:
            return False
    if len(candidate.body_text) > len(existing.body_text):
        return True
    if len(candidate.body_text) < len(existing.body_text):
        return False
    return candidate.folder_name < existing.folder_name


def split_dated_undated(
    messages: list[ParsedMessage],
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[ParsedMessage], list[ParsedMessage], int]:
    """Split into dated (in window) and undated. Returns skipped_outside_window count."""
    dated: list[ParsedMessage] = []
    undated: list[ParsedMessage] = []
    skipped = 0
    for msg in messages:
        if msg.date_parsed is None:
            undated.append(msg)
        elif window_start <= msg.date_parsed <= window_end:
            dated.append(msg)
        else:
            skipped += 1
    return dated, undated, skipped


def _sort_dated(messages: list[ParsedMessage]) -> list[ParsedMessage]:
    return sorted(
        messages,
        key=lambda m: (
            -(m.date_parsed.timestamp() if m.date_parsed else 0),
            m.sender.lower(),
            m.subject.lower(),
        ),
    )


def _sort_undated(messages: list[ParsedMessage]) -> list[ParsedMessage]:
    return sorted(
        messages,
        key=lambda m: (m.folder_name.lower(), m.sender.lower(), m.subject.lower()),
    )


def make_digest_entry(
    classified: ClassifiedMessage,
    no_ollama: bool,
    summary: str | None = None,
    summary_source: SummarySource | None = None,
) -> DigestEntry:
    """Build digest entry with preview fallback for MVP."""
    parsed = classified.parsed
    if summary_source is not None:
        return DigestEntry(
            classified=classified, summary=summary, summary_source=summary_source
        )
    if no_ollama or summary is None:
        if parsed.preview:
            return DigestEntry(
                classified=classified,
                summary=parsed.preview,
                summary_source="preview_fallback",
            )
        return DigestEntry(classified=classified, summary=None, summary_source="none")
    return DigestEntry(
        classified=classified,
        summary=summary,
        summary_source=summary_source or "ollama",
    )


def group_dated_by_folder(
    entries: list[DigestEntry],
) -> dict[str, tuple[DigestEntry, ...]]:
    folders: dict[str, list[DigestEntry]] = {}
    for entry in entries:
        folder = entry.classified.parsed.folder_name
        folders.setdefault(folder, []).append(entry)
    return {k: tuple(v) for k, v in sorted(folders.items())}


def build_digest_entries(
    messages: list[ParsedMessage],
    generated_at: datetime,
    lookback_days: int,
    no_ollama: bool,
) -> tuple[list[DigestEntry], list[DigestEntry], int, int]:
    """Classify and split messages. Returns dated entries, undated entries, skipped, deduped."""
    deduped, dedup_count = dedupe_messages(messages)
    window_start, window_end = compute_date_window(generated_at, lookback_days)
    dated_msgs, undated_msgs, skipped = split_dated_undated(
        deduped, window_start, window_end
    )
    dated_sorted = _sort_dated(dated_msgs)
    undated_sorted = _sort_undated(undated_msgs)

    dated_entries = [
        make_digest_entry(classify_message(m), no_ollama=no_ollama)
        for m in dated_sorted
    ]
    undated_entries = [
        make_digest_entry(classify_message(m), no_ollama=no_ollama)
        for m in undated_sorted
    ]
    return dated_entries, undated_entries, skipped, dedup_count


def apply_undated_seen_filter(
    undated_entries: list[DigestEntry],
    seen_keys: set[str],
    include_seen: bool,
) -> tuple[list[DigestEntry], int]:
    """Filter undated entries by seen_messages. Returns (to_render, skipped_seen)."""
    if include_seen:
        return undated_entries, 0
    to_render: list[DigestEntry] = []
    skipped = 0
    for entry in undated_entries:
        key = entry.classified.parsed.message_key
        if key in seen_keys:
            skipped += 1
        else:
            to_render.append(entry)
    return to_render, skipped


def empty_stats() -> DigestStats:
    return DigestStats(
        folders_scanned=0,
        messages_parsed=0,
        dated_included=0,
        undated_needing_review=0,
        skipped_outside_window=0,
        skipped_seen_undated=0,
        deduped_messages=0,
        parse_errors=0,
        summaries_ollama=0,
        summaries_cache=0,
        summaries_fallback=0,
    )


def count_summary_sources(entries: list[DigestEntry]) -> tuple[int, int, int]:
    ollama = cache = fallback = 0
    for e in entries:
        if e.summary_source == "ollama":
            ollama += 1
        elif e.summary_source == "cache":
            cache += 1
        elif e.summary_source == "preview_fallback":
            fallback += 1
    return ollama, cache, fallback
