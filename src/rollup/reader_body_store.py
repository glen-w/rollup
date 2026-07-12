"""Persistence for message_reader_bodies."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, Sequence

from rollup.payload_limits import SQL_IN_CHUNK_SIZE
from rollup.reader_bodies import (
    BodyUpsertStats,
    READER_TEXT_VERSION,
    ReaderBodyWrite,
    compute_reader_content_hash,
    compute_stored_body_hash,
    prepare_reader_text,
    validate_reader_body_write,
)
from rollup.utc import format_utc, now_utc

MAINTENANCE_GENERATION_KEY = "maintenance_generation"


@dataclass(frozen=True)
class ExistingBodyRow:
    content_hash: str
    stored_body_hash: str


def reader_body_keys_present(
    conn: sqlite3.Connection, keys: Iterable[str]
) -> set[str]:
    unique = list(dict.fromkeys(keys))
    if not unique:
        return set()
    found: set[str] = set()
    for i in range(0, len(unique), SQL_IN_CHUNK_SIZE):
        chunk = unique[i : i + SQL_IN_CHUNK_SIZE]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT message_key FROM message_reader_bodies WHERE message_key IN ({placeholders})",
            chunk,
        ).fetchall()
        found.update(r[0] for r in rows)
    return found


def get_reader_body(conn: sqlite3.Connection, message_key: str):
    from rollup.reader_bodies import ReaderBodyRecord

    row = conn.execute(
        """SELECT message_key, content_hash, stored_body_hash, body_text, truncated,
                  updated_at, last_seen_at, reader_text_version, source_body_length,
                  reader_content_hash, reader_hash_authoritative, first_indexed_at
           FROM message_reader_bodies WHERE message_key = ?""",
        (message_key,),
    ).fetchone()
    if row is None:
        return None
    return ReaderBodyRecord(
        message_key=row[0],
        content_hash=row[1],
        stored_body_hash=row[2],
        body_text=row[3],
        truncated=bool(row[4]),
        updated_at=row[5],
        last_seen_at=row[6],
        reader_text_version=int(row[7] if row[7] is not None else 0),
        source_body_length=int(row[8] if row[8] is not None else -1),
        reader_content_hash=row[9],
        reader_hash_authoritative=bool(row[10]) if row[10] is not None else False,
        first_indexed_at=row[11],
    )


def _fetch_existing(
    conn: sqlite3.Connection, keys: Sequence[str]
) -> dict[str, ExistingBodyRow]:
    if not keys:
        return {}
    out: dict[str, ExistingBodyRow] = {}
    for i in range(0, len(keys), SQL_IN_CHUNK_SIZE):
        chunk = keys[i : i + SQL_IN_CHUNK_SIZE]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"""SELECT message_key, content_hash, stored_body_hash
                FROM message_reader_bodies WHERE message_key IN ({placeholders})""",
            chunk,
        ).fetchall()
        for r in rows:
            out[r[0]] = ExistingBodyRow(content_hash=r[1], stored_body_hash=r[2])
    return out


def _bump_maintenance_generation(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )"""
    )
    row = conn.execute(
        "SELECT value FROM app_metadata WHERE key = ?", (MAINTENANCE_GENERATION_KEY,)
    ).fetchone()
    n = int(row[0]) + 1 if row else 1
    conn.execute(
        """INSERT INTO app_metadata (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (MAINTENANCE_GENERATION_KEY, str(n)),
    )


def upsert_reader_bodies(
    conn: sqlite3.Connection,
    writes: Sequence[ReaderBodyWrite],
    *,
    seen_at: str | None = None,
) -> BodyUpsertStats:
    """Hash-aware upsert inside caller transaction (Stage 1 semantics)."""
    ts = seen_at or format_utc(now_utc())
    if not writes:
        return BodyUpsertStats()
    for w in writes:
        validate_reader_body_write(w)
    existing = _fetch_existing(conn, [w.message_key for w in writes])
    to_insert: list[tuple] = []
    to_update: list[tuple] = []
    to_touch: list[str] = []
    conflicts = 0
    for w in writes:
        ex = existing.get(w.message_key)
        if ex is None:
            to_insert.append(
                (
                    w.message_key,
                    w.content_hash,
                    w.stored_body_hash,
                    w.body_text,
                    1 if w.truncated else 0,
                    ts,
                    ts,
                )
            )
            continue
        if ex.content_hash == w.content_hash and ex.stored_body_hash == w.stored_body_hash:
            to_touch.append(w.message_key)
            continue
        if ex.content_hash == w.content_hash:
            conflicts += 1
            continue
        to_update.append(
            (
                w.content_hash,
                w.stored_body_hash,
                w.body_text,
                1 if w.truncated else 0,
                ts,
                ts,
                w.message_key,
            )
        )
    for row in to_insert:
        conn.execute(
            """INSERT INTO message_reader_bodies (
                message_key, content_hash, stored_body_hash, body_text, truncated,
                updated_at, last_seen_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            row,
        )
    for row in to_update:
        conn.execute(
            """UPDATE message_reader_bodies SET
                content_hash = ?, stored_body_hash = ?, body_text = ?, truncated = ?,
                updated_at = ?, last_seen_at = ?
               WHERE message_key = ?""",
            row,
        )
    for key in to_touch:
        conn.execute(
            "UPDATE message_reader_bodies SET last_seen_at = ? WHERE message_key = ?",
            (ts, key),
        )
    if to_insert or to_update:
        _bump_maintenance_generation(conn)
    return BodyUpsertStats(
        inserted=len(to_insert),
        updated=len(to_update),
        unchanged=len(to_touch),
        conflicts=conflicts,
    )


def upsert_reader_bodies_v2(
    conn: sqlite3.Connection,
    writes: Sequence[ReaderBodyWrite],
    *,
    seen_at: str | None = None,
) -> BodyUpsertStats:
    """Stage 2 upsert with prepared text and provenance columns."""
    ts = seen_at or format_utc(now_utc())
    if not writes:
        return BodyUpsertStats()
    cols = {
        r[1]
        for r in conn.execute("PRAGMA table_info(message_reader_bodies)").fetchall()
    }
    if "reader_text_version" not in cols:
        return upsert_reader_bodies(conn, writes, seen_at=seen_at)
    existing = _fetch_existing(conn, [w.message_key for w in writes])
    to_insert: list[tuple] = []
    to_update: list[tuple] = []
    to_touch: list[str] = []
    conflicts = 0
    for w in writes:
        validate_reader_body_write(w)
        prepared = prepare_reader_text(w.body_text if not w.truncated else w.body_text)
        body_text = prepared.text
        truncated = prepared.truncated
        stored = compute_stored_body_hash(truncated=truncated, body_text=body_text)
        reader_hash = compute_reader_content_hash(
            reader_text_version=prepared.reader_text_version,
            prepared_text=body_text if not truncated else w.body_text,
        )
        ex = existing.get(w.message_key)
        if ex is None:
            to_insert.append(
                (
                    w.message_key,
                    w.content_hash,
                    stored,
                    body_text,
                    1 if truncated else 0,
                    ts,
                    ts,
                    prepared.reader_text_version,
                    prepared.source_body_length,
                    reader_hash,
                    1,
                    ts,
                )
            )
            continue
        if (
            ex.content_hash == w.content_hash
            and ex.stored_body_hash == stored
        ):
            to_touch.append(w.message_key)
            continue
        if ex.content_hash == w.content_hash and ex.stored_body_hash != stored:
            conflicts += 1
            continue
        to_update.append(
            (
                w.content_hash,
                stored,
                body_text,
                1 if truncated else 0,
                ts,
                ts,
                prepared.reader_text_version,
                prepared.source_body_length,
                reader_hash,
                w.message_key,
            )
        )
    for row in to_insert:
        conn.execute(
            """INSERT INTO message_reader_bodies (
                message_key, content_hash, stored_body_hash, body_text, truncated,
                updated_at, last_seen_at, reader_text_version, source_body_length,
                reader_content_hash, reader_hash_authoritative, first_indexed_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row,
        )
    for row in to_update:
        conn.execute(
            """UPDATE message_reader_bodies SET
                content_hash = ?, stored_body_hash = ?, body_text = ?, truncated = ?,
                updated_at = ?, last_seen_at = ?, reader_text_version = ?,
                source_body_length = ?, reader_content_hash = ?, reader_hash_authoritative = 1
               WHERE message_key = ?""",
            row,
        )
    for key in to_touch:
        conn.execute(
            "UPDATE message_reader_bodies SET last_seen_at = ? WHERE message_key = ?",
            (ts, key),
        )
    if to_insert or to_update:
        _bump_maintenance_generation(conn)
    return BodyUpsertStats(
        inserted=len(to_insert),
        updated=len(to_update),
        unchanged=len(to_touch),
        conflicts=conflicts,
    )


def count_reader_bodies(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM message_reader_bodies").fetchone()
    return int(row[0]) if row else 0


def get_maintenance_generation(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT value FROM app_metadata WHERE key = ?", (MAINTENANCE_GENERATION_KEY,)
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0
