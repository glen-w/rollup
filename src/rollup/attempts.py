"""Operational digest attempt log. Not a published artifact."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)

_STATUSES = frozenset({"started", "success", "partial", "failure"})


def start_attempt(conn: sqlite3.Connection, *, run_id: str, started_at: datetime) -> None:
    conn.execute(
        """
        INSERT INTO digest_attempts (run_id, started_at, finished_at, status, error_summary)
        VALUES (?, ?, NULL, 'started', NULL)
        ON CONFLICT(run_id) DO UPDATE SET
            started_at = excluded.started_at,
            finished_at = NULL,
            status = 'started',
            error_summary = NULL
        """,
        (run_id, started_at.isoformat()),
    )
    conn.commit()


def finish_attempt(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    finished_at: datetime,
    error_summary: str | None = None,
) -> None:
    stored = status if status in _STATUSES else "failure"
    summary = (error_summary or "")[:240] or None
    conn.execute(
        """
        UPDATE digest_attempts
        SET finished_at = ?, status = ?, error_summary = ?
        WHERE run_id = ?
        """,
        (finished_at.isoformat(), stored, summary, run_id),
    )
    conn.commit()


def list_attempts(conn: sqlite3.Connection, *, limit: int = 40) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    try:
        return list(
            conn.execute(
                """
                SELECT run_id, started_at, finished_at, status, error_summary
                FROM digest_attempts
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )
    except sqlite3.OperationalError as exc:
        logger.debug("digest_attempts unavailable: %s", exc)
        return []
