"""Message ratings and secondary reason codes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from rollup.payload_limits import MAX_REASON_CODES
from rollup.utc import format_utc
from rollup.web_ids import validate_message_key


class RatingError(ValueError):
    """Invalid rating or reason payload."""


@dataclass(frozen=True)
class ReasonCode:
    code: str
    polarity: str
    label: str
    sort_order: int
    active: bool


@dataclass(frozen=True)
class MessageRating:
    message_key: str
    stars: int
    created_at: str
    updated_at: str
    reason_codes: tuple[str, ...]


def list_reason_codes(conn: sqlite3.Connection, *, active_only: bool = True) -> list[ReasonCode]:
    sql = """SELECT code, polarity, label, sort_order, active
             FROM rating_reason_codes"""
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY sort_order, code"
    rows = conn.execute(sql).fetchall()
    return [
        ReasonCode(
            code=r[0],
            polarity=r[1],
            label=r[2],
            sort_order=int(r[3]),
            active=bool(r[4]),
        )
        for r in rows
    ]


def _allowed_polarities(stars: int) -> frozenset[str]:
    if stars in (1, 2):
        return frozenset({"negative"})
    if stars == 3:
        return frozenset({"positive", "negative"})
    if stars in (4, 5):
        return frozenset({"positive"})
    raise RatingError("stars must be 1-5")


def set_rating_with_reasons(
    conn: sqlite3.Connection,
    message_key: str,
    stars: int,
    reason_codes: Sequence[str],
    *,
    now: datetime,
    commit: bool = True,
) -> MessageRating:
    """Atomically upsert stars and replace-all reason assignments."""
    key = validate_message_key(message_key)
    if not isinstance(stars, int) or stars < 1 or stars > 5:
        raise RatingError("stars must be an integer 1-5")
    codes = list(dict.fromkeys(reason_codes))  # preserve order, dedupe
    if len(codes) > MAX_REASON_CODES:
        raise RatingError("too many reason codes")
    allowed = _allowed_polarities(stars)
    now_s = format_utc(now)

    try:
        if commit:
            conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT created_at FROM message_ratings WHERE message_key = ?",
            (key,),
        ).fetchone()
        created_at = existing[0] if existing else now_s
        conn.execute(
            """INSERT INTO message_ratings (message_key, stars, created_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(message_key) DO UPDATE SET
                 stars=excluded.stars,
                 updated_at=excluded.updated_at""",
            (key, stars, created_at, now_s),
        )
        conn.execute(
            "DELETE FROM message_rating_reasons WHERE message_key = ?", (key,)
        )
        for code in codes:
            row = conn.execute(
                "SELECT polarity, active FROM rating_reason_codes WHERE code = ?",
                (code,),
            ).fetchone()
            if row is None:
                raise RatingError(f"unknown reason code: {code}")
            polarity, active = row[0], row[1]
            if not active:
                raise RatingError(f"inactive reason code: {code}")
            if polarity not in allowed:
                raise RatingError(
                    f"reason {code!r} polarity {polarity!r} incompatible with {stars} stars"
                )
            conn.execute(
                """INSERT INTO message_rating_reasons (message_key, reason_code, created_at)
                   VALUES (?, ?, ?)""",
                (key, code, now_s),
            )
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    return get_rating(conn, key)  # type: ignore[return-value]


def get_rating(conn: sqlite3.Connection, message_key: str) -> MessageRating | None:
    key = validate_message_key(message_key)
    row = conn.execute(
        "SELECT message_key, stars, created_at, updated_at FROM message_ratings WHERE message_key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    reasons = conn.execute(
        """SELECT reason_code FROM message_rating_reasons
           WHERE message_key = ? ORDER BY reason_code""",
        (key,),
    ).fetchall()
    return MessageRating(
        message_key=row[0],
        stars=int(row[1]),
        created_at=row[2],
        updated_at=row[3],
        reason_codes=tuple(r[0] for r in reasons),
    )


def clear_rating(
    conn: sqlite3.Connection,
    message_key: str,
    *,
    commit: bool = True,
) -> None:
    key = validate_message_key(message_key)
    conn.execute("DELETE FROM message_ratings WHERE message_key = ?", (key,))
    if commit:
        conn.commit()
