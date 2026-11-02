"""SQLite cache for raw Gmail messages and per-label sync snapshots (schema v18).

Standing rule for network sources: persist fetched payloads and avoid
re-calling the API when a fresh-enough snapshot exists. Raw RFC 822 bytes
are stored zlib-compressed so parser improvements re-apply without refetch.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from rollup.gmail.config import (
    MAX_RAW_MESSAGE_BYTES,
    GmailConfig,
    GmailTarget,
    expand_prefix_targets,
)
from rollup.gmail.session import (
    GmailClient,
    GmailNotFoundError,
    GmailRawMessage,
    GmailSessionError,
)
from rollup.source_fetch_cache import (
    format_cache_age,
    parse_iso_datetime,
    snapshot_is_fresh,
)

logger = logging.getLogger(__name__)

# Keep raw payloads this many days after they were fetched (retention sweep).
RAW_RETENTION_DAYS = 120


@dataclass(frozen=True)
class GmailLabelSnapshot:
    label_name: str
    label_id: str
    fetched_at: datetime
    message_ids: tuple[str, ...]
    history_id: str | None = None


# -- label snapshots -------------------------------------------------------


def get_label_snapshot(conn: sqlite3.Connection, label_name: str) -> GmailLabelSnapshot | None:
    row = conn.execute(
        """
        SELECT label_name, label_id, history_id, fetched_at, message_ids_json
        FROM gmail_label_sync WHERE label_name = ?
        """,
        (label_name,),
    ).fetchone()
    if not row:
        return None
    fetched_at = parse_iso_datetime(row[3])
    if fetched_at is None:
        return None
    return GmailLabelSnapshot(
        label_name=row[0],
        label_id=row[1],
        history_id=row[2],
        fetched_at=fetched_at,
        message_ids=tuple(json.loads(row[4] or "[]")),
    )


def known_labels(conn: sqlite3.Connection) -> dict[str, str]:
    """``{label_name: label_id}`` from previous syncs (offline label resolution)."""
    rows = conn.execute("SELECT label_name, label_id FROM gmail_label_sync").fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


def list_label_snapshots(conn: sqlite3.Connection) -> list[GmailLabelSnapshot]:
    names = [r[0] for r in conn.execute("SELECT label_name FROM gmail_label_sync ORDER BY label_name")]
    out: list[GmailLabelSnapshot] = []
    for name in names:
        snap = get_label_snapshot(conn, name)
        if snap is not None:
            out.append(snap)
    return out


def save_label_snapshot(
    conn: sqlite3.Connection,
    *,
    label_name: str,
    label_id: str,
    message_ids: list[str],
    fetched_at: datetime,
    history_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO gmail_label_sync (label_name, label_id, history_id, fetched_at, message_ids_json)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(label_name) DO UPDATE SET
            label_id = excluded.label_id,
            history_id = excluded.history_id,
            fetched_at = excluded.fetched_at,
            message_ids_json = excluded.message_ids_json
        """,
        (label_name, label_id, history_id, fetched_at.isoformat(), json.dumps(message_ids)),
    )
    conn.commit()


# -- raw messages ------------------------------------------------------------


def save_raw_message(conn: sqlite3.Connection, msg: GmailRawMessage, *, fetched_at: datetime) -> None:
    conn.execute(
        """
        INSERT INTO gmail_messages (
            gmail_id, thread_id, label_ids_json, internal_date, raw_zlib, raw_bytes, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(gmail_id) DO UPDATE SET
            thread_id = excluded.thread_id,
            label_ids_json = excluded.label_ids_json,
            internal_date = excluded.internal_date,
            raw_zlib = excluded.raw_zlib,
            raw_bytes = excluded.raw_bytes,
            fetched_at = excluded.fetched_at
        """,
        (
            msg.gmail_id,
            msg.thread_id,
            json.dumps(list(msg.label_ids)),
            msg.internal_date.isoformat() if msg.internal_date else None,
            sqlite3.Binary(zlib.compress(msg.raw, 6)),
            len(msg.raw),
            fetched_at.isoformat(),
        ),
    )


def cached_message_ids(conn: sqlite3.Connection, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    found: set[str] = set()
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT gmail_id FROM gmail_messages WHERE gmail_id IN ({placeholders})", chunk
        ).fetchall()
        found.update(str(r[0]) for r in rows)
    return found


def load_raw_messages(conn: sqlite3.Connection, ids: tuple[str, ...] | list[str]) -> list[GmailRawMessage]:
    if not ids:
        return []
    by_id: dict[str, GmailRawMessage] = {}
    id_list = list(ids)
    for start in range(0, len(id_list), 500):
        chunk = id_list[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT gmail_id, thread_id, label_ids_json, internal_date, raw_zlib, raw_bytes
            FROM gmail_messages WHERE gmail_id IN ({placeholders})
            """,
            chunk,
        ).fetchall()
        for row in rows:
            by_id[str(row[0])] = GmailRawMessage(
                gmail_id=str(row[0]),
                thread_id=str(row[1] or ""),
                label_ids=tuple(json.loads(row[2] or "[]")),
                internal_date=parse_iso_datetime(row[3]),
                raw=zlib.decompress(row[4]),
                size_estimate=int(row[5] or 0),
            )
    return [by_id[i] for i in id_list if i in by_id]


def count_cached_messages(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM gmail_messages").fetchone()
    return int(row[0]) if row else 0


def sweep_old_messages(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    retention_days: int = RAW_RETENTION_DAYS,
) -> int:
    """Delete raw payloads fetched more than ``retention_days`` ago."""
    cutoff = (now - timedelta(days=retention_days)).isoformat()
    cur = conn.execute("DELETE FROM gmail_messages WHERE fetched_at < ?", (cutoff,))
    conn.commit()
    return int(cur.rowcount or 0)


# -- fetch policy ------------------------------------------------------------


def should_fetch_label(
    conn: sqlite3.Connection,
    *,
    target: GmailTarget,
    ttl_hours: int,
    refresh: bool,
    now: datetime,
) -> tuple[bool, GmailLabelSnapshot | None]:
    if target.is_prefix:
        return True, None
    snapshot = get_label_snapshot(conn, target.label_name)
    if refresh or ttl_hours <= 0:
        return True, snapshot
    if snapshot is None:
        return True, None
    if not snapshot_is_fresh(snapshot.fetched_at, ttl_hours, now):
        return True, snapshot
    return False, snapshot


def partition_gmail_targets(
    conn: sqlite3.Connection,
    targets: tuple[GmailTarget, ...],
    *,
    ttl_hours: int,
    refresh: bool,
    now: datetime,
) -> tuple[dict[str, list[GmailRawMessage]], list[GmailTarget], list[str]]:
    """Return cached raw messages by label name, targets needing fetch, log lines."""
    cached: dict[str, list[GmailRawMessage]] = {}
    to_fetch: list[GmailTarget] = []
    log_lines: list[str] = []
    for target in targets:
        need, snapshot = should_fetch_label(
            conn, target=target, ttl_hours=ttl_hours, refresh=refresh, now=now
        )
        if need:
            to_fetch.append(target)
            continue
        assert snapshot is not None
        cached[target.label_name] = load_raw_messages(conn, snapshot.message_ids)
        log_lines.append(
            f"Gmail cache hit {target.label_name} "
            f"({len(snapshot.message_ids)} msgs, {format_cache_age(snapshot.fetched_at, now)})"
        )
    return cached, to_fetch, log_lines


def count_targets_needing_fetch(
    conn: sqlite3.Connection,
    targets: tuple[GmailTarget, ...],
    *,
    ttl_hours: int,
    refresh: bool,
    now: datetime,
) -> int:
    return sum(
        1
        for t in targets
        if should_fetch_label(conn, target=t, ttl_hours=ttl_hours, refresh=refresh, now=now)[0]
    )


def gmail_window_query(window_start: datetime, *, extra_query: str | None) -> str:
    """``after:`` is date-granular in the account timezone; pad by one day."""
    after = (window_start - timedelta(days=1)).strftime("%Y/%m/%d")
    parts = [f"after:{after}"]
    if extra_query:
        parts.append(extra_query.strip())
    return " ".join(parts)


def fetch_and_cache_gmail_targets(
    conn: sqlite3.Connection,
    targets: tuple[GmailTarget, ...],
    *,
    config: GmailConfig,
    client: GmailClient,
    window_start: datetime,
    fetched_at: datetime,
    folders_include: tuple[str, ...],
    folders_exclude: tuple[str, ...],
    on_label_fetched=None,
) -> tuple[dict[str, list[GmailRawMessage]], dict[str, str], list[tuple[str, str]], tuple[GmailTarget, ...]]:
    """Resolve labels, fetch new raw messages, persist snapshots.

    Returns ``(messages_by_label, folder_by_label, failures, resolved_targets)``
    where failures are ``(label_name, error)`` pairs. Only message ids not
    already cached are fetched with ``messages.get``.
    """
    messages_by_label: dict[str, list[GmailRawMessage]] = {}
    folder_by_label: dict[str, str] = {}
    failures: list[tuple[str, str]] = []

    try:
        live_labels = client.list_labels()
    except GmailSessionError as exc:
        # Cannot resolve any label: every target fails the same way.
        for target in targets:
            failures.append((target.label_name, str(exc)))
        return messages_by_label, folder_by_label, failures, ()

    resolved = expand_prefix_targets(
        targets,
        config=config,
        live_labels=live_labels,
        folders_include=folders_include,
        folders_exclude=folders_exclude,
    )
    query = gmail_window_query(window_start, extra_query=config.extra_query)
    total = len(resolved)
    for index, target in enumerate(resolved, start=1):
        folder_by_label[target.label_name] = target.folder_name
        if target.label_id is None:
            failures.append((target.label_name, f"Gmail label not found: {target.label_name}"))
            continue
        logger.info("Gmail fetch %d/%d %s", index, total, target.label_name)
        try:
            ids = client.list_message_ids(
                label_id=target.label_id,
                query=query,
                max_results=config.max_messages_per_label,
            )
            already = cached_message_ids(conn, ids)
            fetched: list[GmailRawMessage] = []
            skipped_large = 0
            for gmail_id in ids:
                if gmail_id in already:
                    continue
                try:
                    msg = client.get_message_raw(gmail_id)
                except GmailNotFoundError:
                    continue
                if len(msg.raw) > MAX_RAW_MESSAGE_BYTES:
                    skipped_large += 1
                    continue
                save_raw_message(conn, msg, fetched_at=fetched_at)
                fetched.append(msg)
            conn.commit()
            kept_ids = [i for i in ids if i in already or any(m.gmail_id == i for m in fetched)]
            save_label_snapshot(
                conn,
                label_name=target.label_name,
                label_id=target.label_id,
                message_ids=kept_ids,
                fetched_at=fetched_at,
            )
            messages_by_label[target.label_name] = load_raw_messages(conn, kept_ids)
            if skipped_large:
                logger.warning(
                    "Gmail %s: skipped %d message(s) over %d bytes",
                    target.label_name,
                    skipped_large,
                    MAX_RAW_MESSAGE_BYTES,
                )
            if on_label_fetched is not None:
                on_label_fetched(target, len(kept_ids), len(fetched))
        except GmailSessionError as exc:
            conn.rollback()
            failures.append((target.label_name, str(exc)))
    return messages_by_label, folder_by_label, failures, resolved
