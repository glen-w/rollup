"""SQLite CRUD for the webpage reading queue."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from rollup.webpage.models import WebpageQueueItem, WebpageQueueStatus
from rollup.webpage.url import url_hash, validate_queue_url

_ROW_SELECT = """
SELECT id, url, url_hash, display_title, status, error_code, error_message,
       created_at, ingested_at, ingested_message_key, ingested_run_id,
       fetched_title, body_text, content_hash, fetched_at
FROM webpage_queue
"""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_row(row: tuple) -> WebpageQueueItem:
    return WebpageQueueItem(
        id=row[0],
        url=row[1],
        url_hash=row[2],
        display_title=row[3],
        status=row[4],
        error_code=row[5],
        error_message=row[6],
        created_at=_parse_dt(row[7]) or datetime.now(timezone.utc),
        ingested_at=_parse_dt(row[8]),
        ingested_message_key=row[9],
        ingested_run_id=row[10],
        fetched_title=row[11],
        body_text=row[12],
        content_hash=row[13],
        fetched_at=_parse_dt(row[14]),
    )


def count_pending(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM webpage_queue WHERE status = 'pending'"
    ).fetchone()
    return int(row[0]) if row else 0


def count_items(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM webpage_queue").fetchone()
    return int(row[0]) if row else 0


def load_pending(conn: sqlite3.Connection, *, limit: int | None = None) -> tuple[WebpageQueueItem, ...]:
    sql = f"{_ROW_SELECT} WHERE status = 'pending' ORDER BY created_at ASC, id ASC"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    return tuple(_parse_row(r) for r in rows)


def list_by_status(
    conn: sqlite3.Connection,
    status: WebpageQueueStatus,
    *,
    limit: int = 50,
) -> tuple[WebpageQueueItem, ...]:
    rows = conn.execute(
        f"{_ROW_SELECT} WHERE status = ? ORDER BY created_at DESC, id DESC LIMIT ?",
        (status, limit),
    ).fetchall()
    return tuple(_parse_row(r) for r in rows)


def get_by_id(conn: sqlite3.Connection, item_id: int) -> WebpageQueueItem | None:
    row = conn.execute(f"{_ROW_SELECT} WHERE id = ?", (item_id,)).fetchone()
    return _parse_row(row) if row else None


def load_for_digest(
    conn: sqlite3.Connection,
    *,
    window_start: datetime,
    window_end: datetime,
    fetch_limit: int,
) -> tuple[WebpageQueueItem, ...]:
    """Pending (to fetch) plus ingested items saved inside the lookback window.

    Cached bodies are reused; rows without a body count against ``fetch_limit``.
    Failed rows stay out until the user retries.
    """
    start = _aware(window_start)
    end = _aware(window_end)
    rows = conn.execute(
        f"{_ROW_SELECT} WHERE status IN ('pending', 'ingested') "
        "ORDER BY created_at ASC, id ASC"
    ).fetchall()
    cached: list[WebpageQueueItem] = []
    need_fetch: list[WebpageQueueItem] = []
    for row in rows:
        item = _parse_row(row)
        saved = _aware(item.created_at)
        in_window = start <= saved <= end
        if item.status == "pending":
            need_fetch.append(item)
            continue
        if not in_window:
            continue
        if item.has_cached_body:
            cached.append(item)
        else:
            need_fetch.append(item)
    selected_fetch = need_fetch[: max(0, int(fetch_limit))]
    by_id = {item.id: item for item in cached}
    for item in selected_fetch:
        by_id[item.id] = item
    return tuple(sorted(by_id.values(), key=lambda i: (_aware(i.created_at), i.id)))


def count_in_window(
    conn: sqlite3.Connection,
    *,
    window_start: datetime,
    window_end: datetime,
) -> int:
    """Pending rows plus ingested rows whose save time falls in the window."""
    return len(
        load_for_digest(
            conn,
            window_start=window_start,
            window_end=window_end,
            fetch_limit=10_000,
        )
    )


def enqueue_url(
    conn: sqlite3.Connection,
    raw_url: str,
    *,
    display_title: str | None = None,
    now: datetime | None = None,
) -> WebpageQueueItem:
    """Add URL to the corpus. Duplicate pending/ingested is a no-op; failed resets to pending."""
    canonical = validate_queue_url(raw_url)
    digest = url_hash(canonical)
    title = display_title.strip() if display_title and display_title.strip() else None
    created = (now or datetime.now(timezone.utc)).isoformat()

    existing = conn.execute(
        f"{_ROW_SELECT} WHERE url_hash = ?",
        (digest,),
    ).fetchone()
    if existing:
        item = _parse_row(existing)
        if item.status in {"pending", "ingested"}:
            if title and title != item.display_title:
                conn.execute(
                    "UPDATE webpage_queue SET display_title = ? WHERE id = ?",
                    (title, item.id),
                )
                conn.commit()
                updated = get_by_id(conn, item.id)
                assert updated is not None
                return updated
            return item
        conn.execute(
            """UPDATE webpage_queue
               SET status = 'pending', url = ?, display_title = COALESCE(?, display_title),
                   error_code = NULL, error_message = NULL,
                   ingested_at = NULL, ingested_message_key = NULL, ingested_run_id = NULL,
                   fetched_title = NULL, body_text = NULL, content_hash = NULL, fetched_at = NULL
               WHERE id = ?""",
            (canonical, title, item.id),
        )
        conn.commit()
        updated = get_by_id(conn, item.id)
        assert updated is not None
        return updated

    cur = conn.execute(
        """INSERT INTO webpage_queue
           (url, url_hash, display_title, status, created_at)
           VALUES (?, ?, ?, 'pending', ?)""",
        (canonical, digest, title, created),
    )
    conn.commit()
    item = get_by_id(conn, int(cur.lastrowid))
    assert item is not None
    return item


def remove_item(conn: sqlite3.Connection, item_id: int) -> bool:
    cur = conn.execute("DELETE FROM webpage_queue WHERE id = ?", (item_id,))
    conn.commit()
    return cur.rowcount > 0


def retry_item(conn: sqlite3.Connection, item_id: int) -> WebpageQueueItem | None:
    item = get_by_id(conn, item_id)
    if item is None or item.status != "failed":
        return item
    conn.execute(
        """UPDATE webpage_queue
           SET status = 'pending', error_code = NULL, error_message = NULL
           WHERE id = ?""",
        (item_id,),
    )
    conn.commit()
    return get_by_id(conn, item_id)


def mark_failed(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    error_code: str,
    error_message: str | None = None,
) -> None:
    conn.execute(
        """UPDATE webpage_queue
           SET status = 'failed', error_code = ?, error_message = ?
           WHERE id = ?""",
        (error_code, error_message, item_id),
    )
    conn.commit()


def store_fetched(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    title: str | None,
    body_text: str,
    content_hash: str,
    message_key: str,
    fetched_at: datetime,
) -> None:
    """Persist fetched article text so later digests can reuse it without a network fetch."""
    conn.execute(
        """UPDATE webpage_queue
           SET status = 'ingested', fetched_title = ?, body_text = ?, content_hash = ?,
               ingested_message_key = ?, fetched_at = ?,
               error_code = NULL, error_message = NULL
           WHERE id = ?""",
        (
            title,
            body_text,
            content_hash,
            message_key,
            fetched_at.isoformat(),
            item_id,
        ),
    )
    conn.commit()


def mark_ingested(
    conn: sqlite3.Connection,
    items: list[tuple[int, str, str]],
    *,
    ingested_at: datetime,
) -> None:
    """Record which run last included these rows. items: (id, message_key, run_id)."""
    if not items:
        return
    iso = ingested_at.isoformat()
    conn.executemany(
        """UPDATE webpage_queue
           SET status = 'ingested', ingested_at = ?, ingested_message_key = ?,
               ingested_run_id = ?, error_code = NULL, error_message = NULL
           WHERE id = ?""",
        [(iso, mk, run_id, item_id) for item_id, mk, run_id in items],
    )
    conn.commit()
