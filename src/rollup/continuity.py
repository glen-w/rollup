"""Cross-run continuity labels. Chrome only; never drops included mail."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, replace

from rollup.attention.annotate import item_attention_rank
from rollup.attention.profile import jaccard
from rollup.briefing import included_message_keys, iter_digest_entries
from rollup.coverage.cluster import TITLE_JACCARD_MIN
from rollup.coverage.normalize import canonical_url, entry_main_urls, normalize_title, title_tokens
from rollup.models import DigestEntry, DigestGroup, DigestItem, DigestReport

logger = logging.getLogger(__name__)

KEEP_RUNS = 26
CONTINUITY_RANK = {"continuing": 0, "repeat": 1, "new": 2}


@dataclass(frozen=True)
class ContinuityRow:
    run_id: str
    window_end: str
    message_key: str
    canonical_url: str | None
    title_norm: str
    folder_name: str


def load_prior_rows(conn: sqlite3.Connection, *, exclude_run_id: str | None = None) -> list[ContinuityRow]:
    sql = """
        SELECT run_id, window_end, message_key, canonical_url, title_norm, folder_name
        FROM briefing_continuity
    """
    params: tuple = ()
    if exclude_run_id:
        sql += " WHERE run_id != ?"
        params = (exclude_run_id,)
    rows = conn.execute(sql, params).fetchall()
    return [
        ContinuityRow(
            run_id=row[0],
            window_end=row[1] or "",
            message_key=row[2],
            canonical_url=row[3],
            title_norm=row[4] or "",
            folder_name=row[5] or "",
        )
        for row in rows
    ]


def previous_run_id(rows: list[ContinuityRow]) -> str | None:
    if not rows:
        return None
    latest = max(rows, key=lambda row: (row.window_end, row.run_id))
    return latest.run_id


def _matches(entry: DigestEntry, row: ContinuityRow) -> bool:
    urls = {url for url in entry_main_urls(entry) if url}
    if row.canonical_url and row.canonical_url in urls:
        return True
    if not row.title_norm:
        return False
    tokens = title_tokens(entry.classified.parsed.subject)
    prior = title_tokens(row.title_norm)
    if not tokens or not prior:
        return False
    return jaccard(tokens, prior) >= TITLE_JACCARD_MIN


def classify_entry(entry: DigestEntry, rows: list[ContinuityRow], *, prev_run: str | None) -> str:
    hits = [row for row in rows if _matches(entry, row)]
    if not hits:
        return "new"
    run_ids = {row.run_id for row in hits}
    if len(run_ids) >= 2:
        return "continuing"
    in_prev = [row for row in hits if prev_run is not None and row.run_id == prev_run]
    if not in_prev:
        return "continuing"
    folder = entry.classified.parsed.folder_name
    urls = {url for url in entry_main_urls(entry) if url}
    same_url = any(row.canonical_url and row.canonical_url in urls for row in in_prev)
    different_folder = any(row.folder_name and row.folder_name != folder for row in in_prev)
    if different_folder:
        return "continuing"
    if same_url or in_prev:
        return "repeat"
    return "new"


def _label_entry(entry: DigestEntry, rows: list[ContinuityRow], *, prev_run: str | None) -> DigestEntry:
    label = classify_entry(entry, rows, prev_run=prev_run)
    if entry.continuity_label == label:
        return entry
    return replace(entry, continuity_label=label)


def _label_item(item: DigestItem, rows: list[ContinuityRow], *, prev_run: str | None) -> DigestItem:
    if isinstance(item, DigestGroup):
        entries = tuple(_label_entry(entry, rows, prev_run=prev_run) for entry in item.entries)
        return replace(item, entries=entries)
    return _label_entry(item, rows, prev_run=prev_run)


def _continuity_rank(item: DigestItem) -> int:
    if isinstance(item, DigestGroup):
        ranks = [CONTINUITY_RANK.get(entry.continuity_label or "new", 2) for entry in item.entries]
        return min(ranks) if ranks else 2
    return CONTINUITY_RANK.get(item.continuity_label or "new", 2)


def sort_items_by_continuity(items: tuple[DigestItem, ...]) -> tuple[DigestItem, ...]:
    """Stable: attention band, then continuing / repeat / new. Tie-break keeps prior order."""
    return tuple(sorted(items, key=lambda item: (item_attention_rank(item), _continuity_rank(item))))


def apply_continuity(
    dated: tuple[DigestItem, ...],
    undated: tuple[DigestItem, ...],
    *,
    conn: sqlite3.Connection | None,
    run_id: str | None = None,
) -> tuple[tuple[DigestItem, ...], tuple[DigestItem, ...], bool]:
    """Annotate entries from the prior published ledger. Empty ledger labels everything new.

    The third value is True when the ledger could not be read (synthesis should degrade).
    """
    before = included_message_keys(dated) + included_message_keys(undated)
    if conn is None:
        return dated, undated, False
    try:
        rows = load_prior_rows(conn, exclude_run_id=run_id)
    except sqlite3.Error as exc:
        logger.warning("Continuity ledger unreadable: %s", exc)
        return dated, undated, True
    prev = previous_run_id(rows)
    dated_out = tuple(_label_item(item, rows, prev_run=prev) for item in dated)
    undated_out = tuple(_label_item(item, rows, prev_run=prev) for item in undated)
    dated_out = sort_items_by_continuity(dated_out)
    undated_out = sort_items_by_continuity(undated_out)
    after = included_message_keys(dated_out) + included_message_keys(undated_out)
    if set(before) != set(after):
        raise RuntimeError("continuity changed the included message set")
    return dated_out, undated_out, False


def continuity_counts(report: DigestReport) -> dict[str, int]:
    counts = {"new": 0, "continuing": 0, "repeat": 0}
    entries = list(iter_digest_entries(
        item for items in report.dated_by_folder.values() for item in items
    ))
    entries.extend(iter_digest_entries(report.undated))
    for entry in entries:
        label = entry.continuity_label
        if label in counts:
            counts[label] += 1
    return counts


def _theme_for(entry: DigestEntry, report: DigestReport) -> str | None:
    cluster_id = entry.coverage_cluster_id
    if not cluster_id:
        return None
    for cluster in report.coverage_clusters:
        if cluster.cluster_id == cluster_id:
            return cluster.theme_label
    return None


def record_continuity(conn: sqlite3.Connection, report: DigestReport, *, run_id: str) -> None:
    """Persist this published window. Keeps the last KEEP_RUNS published runs."""
    window_end = report.window_end.isoformat()
    conn.execute("DELETE FROM briefing_continuity WHERE run_id = ?", (run_id,))
    entries = list(iter_digest_entries(
        item for items in report.dated_by_folder.values() for item in items
    ))
    entries.extend(iter_digest_entries(report.undated))
    for entry in entries:
        urls = entry_main_urls(entry)
        url = urls[0] if urls else None
        if url:
            url = canonical_url(url) or url
        conn.execute(
            """
            INSERT INTO briefing_continuity (
                run_id, window_end, message_key, canonical_url, title_norm,
                attention_band, theme_label, folder_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                window_end,
                entry.classified.parsed.message_key,
                url,
                normalize_title(entry.classified.parsed.subject),
                entry.attention_band,
                _theme_for(entry, report),
                entry.classified.parsed.folder_name,
            ),
        )
    run_ids = [
        row[0]
        for row in conn.execute(
            """
            SELECT run_id FROM briefing_continuity
            GROUP BY run_id
            ORDER BY MAX(window_end) DESC
            """
        ).fetchall()
    ]
    drop = run_ids[KEEP_RUNS:]
    if drop:
        placeholders = ",".join("?" for _ in drop)
        conn.execute(
            f"DELETE FROM briefing_continuity WHERE run_id IN ({placeholders})",
            drop,
        )
    conn.commit()
