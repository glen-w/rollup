"""SQLite FTS5 convenience-cache index over reader bodies, titles, and summaries.

This is not a second mailbox. Missing bodies are indexed from title/summary
only. Reader bodies never appear in manifests.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SEARCH_LIMIT_DEFAULT = 20
SEARCH_LIMIT_MAX = 50
ASK_TOP_K = 8
ASK_EXCERPT_CHARS = 800
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_FTS_TABLE = "message_corpus_fts"


@dataclass(frozen=True)
class CorpusHit:
    message_key: str
    title: str
    sender: str
    folder: str
    snippet: str
    summary: str
    rank: float


@dataclass(frozen=True)
class CorpusHealth:
    table_present: bool
    fts_rows: int
    body_rows: int
    entry_keys: int


def fts5_available() -> bool:
    """True when this SQLite build can create an FTS5 virtual table."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def corpus_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_FTS_TABLE,),
    ).fetchone()
    return row is not None


def corpus_health(conn: sqlite3.Connection) -> CorpusHealth:
    present = corpus_table_exists(conn)
    fts_rows = 0
    if present:
        row = conn.execute(f"SELECT COUNT(*) FROM {_FTS_TABLE}").fetchone()
        fts_rows = int(row[0] or 0)
    body_rows = 0
    entry_keys = 0
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if "message_reader_bodies" in tables:
        row = conn.execute("SELECT COUNT(*) FROM message_reader_bodies").fetchone()
        body_rows = int(row[0] or 0)
    if "rollup_entries" in tables:
        row = conn.execute(
            "SELECT COUNT(DISTINCT message_key) FROM rollup_entries"
        ).fetchone()
        entry_keys = int(row[0] or 0)
    return CorpusHealth(
        table_present=present,
        fts_rows=fts_rows,
        body_rows=body_rows,
        entry_keys=entry_keys,
    )


def fts_match_query(raw: str) -> str:
    """Turn a user string into a bound FTS5 MATCH query (quoted AND tokens)."""
    tokens = _TOKEN_RE.findall(raw or "")
    if not tokens:
        return ""
    return " AND ".join(f'"{tok}"' for tok in tokens[:24])


def upsert_corpus_row(
    conn: sqlite3.Connection,
    *,
    message_key: str,
    title: str = "",
    sender: str = "",
    folder: str = "",
    body_text: str = "",
    summary: str = "",
) -> None:
    """Replace one FTS row. No-op when the v17 table is absent."""
    if not message_key or not corpus_table_exists(conn):
        return
    conn.execute(f"DELETE FROM {_FTS_TABLE} WHERE message_key = ?", (message_key,))
    conn.execute(
        f"""INSERT INTO {_FTS_TABLE}
            (message_key, title, sender, folder, body_text, summary)
            VALUES (?, ?, ?, ?, ?, ?)""",
        (
            message_key,
            title or "",
            sender or "",
            folder or "",
            body_text or "",
            summary or "",
        ),
    )


def rebuild_corpus(conn: sqlite3.Connection) -> int:
    """Replace all FTS rows from bodies + latest rollup_entries. Caller owns txn."""
    if not corpus_table_exists(conn):
        return 0
    conn.execute(f"DELETE FROM {_FTS_TABLE}")
    conn.execute(
        f"""
        INSERT INTO {_FTS_TABLE}
            (message_key, title, sender, folder, body_text, summary)
        SELECT
            keys.message_key,
            COALESCE(e.subject, ''),
            COALESCE(e.sender, ''),
            COALESCE(e.folder_name, ''),
            COALESCE(b.body_text, ''),
            COALESCE(e.summary, '')
        FROM (
            SELECT message_key FROM message_reader_bodies
            UNION
            SELECT message_key FROM rollup_entries
        ) AS keys
        LEFT JOIN message_reader_bodies AS b
            ON b.message_key = keys.message_key
        LEFT JOIN (
            SELECT e1.message_key, e1.subject, e1.sender, e1.folder_name, e1.summary
            FROM rollup_entries AS e1
            INNER JOIN (
                SELECT message_key, MAX(rowid) AS rid
                FROM rollup_entries
                GROUP BY message_key
            ) AS latest ON latest.rid = e1.rowid
        ) AS e ON e.message_key = keys.message_key
        """
    )
    row = conn.execute(f"SELECT COUNT(*) FROM {_FTS_TABLE}").fetchone()
    return int(row[0] or 0)


def sync_corpus_keys(conn: sqlite3.Connection, keys: list[str]) -> int:
    """Refresh FTS rows for the given message keys. Caller owns txn."""
    unique = [k for k in dict.fromkeys(keys) if k]
    if not unique or not corpus_table_exists(conn):
        return 0
    n = 0
    for key in unique:
        meta = _latest_entry_meta(conn, key)
        body = _body_text(conn, key)
        upsert_corpus_row(
            conn,
            message_key=key,
            title=meta.get("subject") or "",
            sender=meta.get("sender") or "",
            folder=meta.get("folder_name") or "",
            body_text=body,
            summary=meta.get("summary") or "",
        )
        n += 1
    return n


def search_corpus(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = SEARCH_LIMIT_DEFAULT,
) -> tuple[CorpusHit, ...]:
    """Ranked FTS hits. Empty or invalid queries return no hits."""
    if not corpus_table_exists(conn):
        return ()
    cap = max(1, min(int(limit), SEARCH_LIMIT_MAX))
    raw = (query or "").strip()
    if not raw:
        return ()
    exact = _exact_key_hit(conn, raw)
    match = fts_match_query(raw)
    hits: list[CorpusHit] = []
    seen: set[str] = set()
    if exact is not None:
        hits.append(exact)
        seen.add(exact.message_key)
    if match:
        try:
            rows = conn.execute(
                f"""
                SELECT message_key, title, sender, folder, summary,
                       snippet({_FTS_TABLE}, 4, '[', ']', '…', 16),
                       bm25({_FTS_TABLE})
                FROM {_FTS_TABLE}
                WHERE {_FTS_TABLE} MATCH ?
                ORDER BY bm25({_FTS_TABLE}) ASC, message_key ASC
                LIMIT ?
                """,
                (match, cap),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            logger.info("FTS MATCH rejected query %r: %s", raw, exc)
            rows = []
        for row in rows:
            key = str(row[0] or "")
            if not key or key in seen:
                continue
            hits.append(
                CorpusHit(
                    message_key=key,
                    title=str(row[1] or ""),
                    sender=str(row[2] or ""),
                    folder=str(row[3] or ""),
                    summary=str(row[4] or ""),
                    snippet=str(row[5] or ""),
                    rank=float(row[6] or 0.0),
                )
            )
            seen.add(key)
            if len(hits) >= cap:
                break
    return tuple(hits[:cap])


def _exact_key_hit(conn: sqlite3.Connection, raw: str) -> CorpusHit | None:
    if ":" not in raw:
        return None
    row = conn.execute(
        f"""
        SELECT message_key, title, sender, folder, summary, body_text
        FROM {_FTS_TABLE}
        WHERE message_key = ?
        """,
        (raw,),
    ).fetchone()
    if row is None:
        return None
    body = str(row[5] or "")
    snippet = body[:180] + ("…" if len(body) > 180 else "")
    return CorpusHit(
        message_key=str(row[0]),
        title=str(row[1] or ""),
        sender=str(row[2] or ""),
        folder=str(row[3] or ""),
        summary=str(row[4] or ""),
        snippet=snippet,
        rank=0.0,
    )


def _latest_entry_meta(conn: sqlite3.Connection, message_key: str) -> dict[str, str]:
    row = conn.execute(
        """
        SELECT subject, sender, folder_name, summary
        FROM rollup_entries
        WHERE message_key = ?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (message_key,),
    ).fetchone()
    if row is None:
        return {}
    return {
        "subject": str(row[0] or ""),
        "sender": str(row[1] or ""),
        "folder_name": str(row[2] or ""),
        "summary": str(row[3] or ""),
    }


def _body_text(conn: sqlite3.Connection, message_key: str) -> str:
    row = conn.execute(
        "SELECT body_text FROM message_reader_bodies WHERE message_key = ?",
        (message_key,),
    ).fetchone()
    return str(row[0] or "") if row else ""
