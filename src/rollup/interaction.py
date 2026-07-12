"""Orthogonal message interaction state (read / saved / dismissed)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from rollup.utc import format_utc
from rollup.web_ids import validate_message_key


@dataclass(frozen=True)
class InteractionState:
    message_key: str
    read_at: str | None
    saved_at: str | None
    dismissed_at: str | None
    updated_at: str | None

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    @property
    def is_saved(self) -> bool:
        return self.saved_at is not None

    @property
    def is_dismissed(self) -> bool:
        return self.dismissed_at is not None


def get_interaction(conn: sqlite3.Connection, message_key: str) -> InteractionState:
    key = validate_message_key(message_key)
    row = conn.execute(
        """SELECT message_key, read_at, saved_at, dismissed_at, updated_at
           FROM message_interaction WHERE message_key = ?""",
        (key,),
    ).fetchone()
    if row is None:
        return InteractionState(
            message_key=key,
            read_at=None,
            saved_at=None,
            dismissed_at=None,
            updated_at=None,
        )
    return InteractionState(
        message_key=row[0],
        read_at=row[1],
        saved_at=row[2],
        dismissed_at=row[3],
        updated_at=row[4],
    )


def _upsert(
    conn: sqlite3.Connection,
    key: str,
    *,
    read_at: str | None,
    saved_at: str | None,
    dismissed_at: str | None,
    updated_at: str,
    commit: bool,
) -> InteractionState:
    conn.execute(
        """INSERT INTO message_interaction
           (message_key, read_at, saved_at, dismissed_at, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(message_key) DO UPDATE SET
             read_at=excluded.read_at,
             saved_at=excluded.saved_at,
             dismissed_at=excluded.dismissed_at,
             updated_at=excluded.updated_at""",
        (key, read_at, saved_at, dismissed_at, updated_at),
    )
    if commit:
        conn.commit()
    return get_interaction(conn, key)


def mark_read(
    conn: sqlite3.Connection, message_key: str, *, now: datetime, commit: bool = True
) -> InteractionState:
    cur = get_interaction(conn, message_key)
    now_s = format_utc(now)
    return _upsert(
        conn,
        cur.message_key,
        read_at=cur.read_at or now_s,
        saved_at=cur.saved_at,
        dismissed_at=cur.dismissed_at,
        updated_at=now_s,
        commit=commit,
    )


def mark_unread(
    conn: sqlite3.Connection, message_key: str, *, now: datetime, commit: bool = True
) -> InteractionState:
    cur = get_interaction(conn, message_key)
    now_s = format_utc(now)
    return _upsert(
        conn,
        cur.message_key,
        read_at=None,
        saved_at=cur.saved_at,
        dismissed_at=cur.dismissed_at,
        updated_at=now_s,
        commit=commit,
    )


def save(
    conn: sqlite3.Connection, message_key: str, *, now: datetime, commit: bool = True
) -> InteractionState:
    cur = get_interaction(conn, message_key)
    now_s = format_utc(now)
    return _upsert(
        conn,
        cur.message_key,
        read_at=cur.read_at or now_s,
        saved_at=now_s,
        dismissed_at=cur.dismissed_at,
        updated_at=now_s,
        commit=commit,
    )


def unsave(
    conn: sqlite3.Connection, message_key: str, *, now: datetime, commit: bool = True
) -> InteractionState:
    cur = get_interaction(conn, message_key)
    now_s = format_utc(now)
    return _upsert(
        conn,
        cur.message_key,
        read_at=cur.read_at,
        saved_at=None,
        dismissed_at=cur.dismissed_at,
        updated_at=now_s,
        commit=commit,
    )


def dismiss(
    conn: sqlite3.Connection, message_key: str, *, now: datetime, commit: bool = True
) -> InteractionState:
    cur = get_interaction(conn, message_key)
    now_s = format_utc(now)
    return _upsert(
        conn,
        cur.message_key,
        read_at=cur.read_at,
        saved_at=cur.saved_at,
        dismissed_at=now_s,
        updated_at=now_s,
        commit=commit,
    )


def undismiss(
    conn: sqlite3.Connection, message_key: str, *, now: datetime, commit: bool = True
) -> InteractionState:
    cur = get_interaction(conn, message_key)
    now_s = format_utc(now)
    return _upsert(
        conn,
        cur.message_key,
        read_at=cur.read_at,
        saved_at=cur.saved_at,
        dismissed_at=None,
        updated_at=now_s,
        commit=commit,
    )
