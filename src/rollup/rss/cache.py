"""SQLite cache for RSS / Atom feeds (schema v19)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from rollup.rss.config import RssConfig, RssFeed
from rollup.rss.fetch import RssFetchError, fetch_feed
from rollup.rss.models import RssItem
from rollup.source_fetch_cache import format_cache_age, parse_iso_datetime, snapshot_is_fresh

RAW_RETENTION_DAYS = 120


@dataclass(frozen=True)
class RssFeedSnapshot:
    feed_url: str
    etag: str | None
    last_modified: str | None
    fetched_at: datetime
    item_keys: tuple[str, ...]


def get_feed_snapshot(conn: sqlite3.Connection, feed_url: str) -> RssFeedSnapshot | None:
    row = conn.execute(
        """
        SELECT feed_url, etag, last_modified, fetched_at, item_keys_json
        FROM rss_feeds WHERE feed_url = ?
        """,
        (feed_url,),
    ).fetchone()
    if not row:
        return None
    fetched_at = parse_iso_datetime(row[3])
    if fetched_at is None:
        return None
    return RssFeedSnapshot(
        feed_url=row[0],
        etag=row[1],
        last_modified=row[2],
        fetched_at=fetched_at,
        item_keys=tuple(json.loads(row[4] or "[]")),
    )


def save_feed_snapshot(
    conn: sqlite3.Connection,
    *,
    feed_url: str,
    etag: str | None,
    last_modified: str | None,
    fetched_at: datetime,
    item_keys: list[str],
) -> None:
    conn.execute(
        """
        INSERT INTO rss_feeds (feed_url, etag, last_modified, fetched_at, item_keys_json)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(feed_url) DO UPDATE SET
            etag = excluded.etag,
            last_modified = excluded.last_modified,
            fetched_at = excluded.fetched_at,
            item_keys_json = excluded.item_keys_json
        """,
        (feed_url, etag, last_modified, fetched_at.isoformat(), json.dumps(item_keys)),
    )


def save_items(conn: sqlite3.Connection, items: list[RssItem], *, fetched_at: datetime) -> None:
    for item in items:
        published = item.published_at.isoformat() if item.published_at else None
        conn.execute(
            """
            INSERT INTO rss_items (
                item_key, feed_url, title, link, author, published_at,
                body_text, raw_bytes, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_key) DO UPDATE SET
                title = excluded.title,
                link = excluded.link,
                author = excluded.author,
                published_at = excluded.published_at,
                body_text = excluded.body_text,
                raw_bytes = excluded.raw_bytes,
                fetched_at = excluded.fetched_at
            """,
            (
                item.item_key,
                item.feed_url,
                item.title,
                item.link,
                item.author,
                published,
                item.body_text,
                item.raw_bytes,
                fetched_at.isoformat(),
            ),
        )


def load_items(conn: sqlite3.Connection, item_keys: tuple[str, ...] | list[str]) -> list[RssItem]:
    if not item_keys:
        return []
    found: dict[str, RssItem] = {}
    keys = list(item_keys)
    for start in range(0, len(keys), 200):
        chunk = keys[start : start + 200]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT item_key, feed_url, title, link, author, published_at,
                   body_text, raw_bytes
            FROM rss_items WHERE item_key IN ({placeholders})
            """,
            chunk,
        ).fetchall()
        for row in rows:
            found[row[0]] = RssItem(
                item_key=row[0],
                feed_url=row[1],
                title=row[2] or "",
                link=row[3],
                author=row[4],
                published_at=parse_iso_datetime(row[5]),
                body_text=row[6] or "",
                raw_bytes=int(row[7] or 0),
            )
    return [found[key] for key in keys if key in found]


def sweep_old_items(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    retention_days: int = RAW_RETENTION_DAYS,
) -> int:
    cutoff = (now - timedelta(days=retention_days)).isoformat()
    cur = conn.execute("DELETE FROM rss_items WHERE fetched_at < ?", (cutoff,))
    conn.commit()
    return int(cur.rowcount or 0)


def count_feeds_needing_fetch(
    conn: sqlite3.Connection,
    feeds: tuple[RssFeed, ...],
    *,
    ttl_hours: int,
    refresh: bool,
    now: datetime,
) -> int:
    count = 0
    for feed in feeds:
        snap = get_feed_snapshot(conn, feed.url)
        if refresh or snap is None or not snapshot_is_fresh(snap.fetched_at, ttl_hours, now):
            count += 1
    return count


def items_in_window(
    items: list[RssItem],
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[RssItem]:
    """Keep undated items. Drop items whose timestamp falls outside the window."""
    kept: list[RssItem] = []
    for item in items:
        if item.published_at is None:
            kept.append(item)
            continue
        published = item.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=window_start.tzinfo)
        if window_start <= published <= window_end:
            kept.append(item)
    return kept


def fetch_and_cache_feeds(
    conn: sqlite3.Connection,
    feeds: tuple[RssFeed, ...],
    *,
    config: RssConfig,
    refresh: bool,
    now: datetime,
    window_start: datetime,
    window_end: datetime,
    session=None,
) -> tuple[dict[str, list[RssItem]], list[tuple[str, str]], list[str]]:
    """Return items by feed URL, failures ``(url, message)``, and log lines.

    A fresh snapshot makes no network call. Fetch failure reuses a stale
    snapshot when one exists.
    """
    by_url: dict[str, list[RssItem]] = {}
    failures: list[tuple[str, str]] = []
    logs: list[str] = []
    total = len(feeds)
    for index, feed in enumerate(feeds, start=1):
        snap = get_feed_snapshot(conn, feed.url)
        fresh = (
            snap is not None
            and not refresh
            and snapshot_is_fresh(snap.fetched_at, config.fetch_ttl_hours, now)
        )
        if fresh and snap is not None:
            cached = items_in_window(
                load_items(conn, snap.item_keys),
                window_start=window_start,
                window_end=window_end,
            )
            by_url[feed.url] = cached
            logs.append(
                f"RSS cache hit {feed.slug} "
                f"({len(cached)} items, {format_cache_age(snap.fetched_at, now)})"
            )
            continue
        logs.append(f"RSS fetch {index}/{total} {feed.slug}")
        try:
            _status, etag, modified, items = fetch_feed(
                feed.url,
                etag=snap.etag if snap else None,
                last_modified=snap.last_modified if snap else None,
                session=session,
            )
        except RssFetchError as exc:
            if snap is not None:
                cached = items_in_window(
                    load_items(conn, snap.item_keys),
                    window_start=window_start,
                    window_end=window_end,
                )
                by_url[feed.url] = cached
                failures.append((feed.url, f"rss_cache_stale: {exc}"))
            else:
                failures.append((feed.url, f"{exc.code}: {exc}"))
            continue
        if items is None and snap is not None:
            save_feed_snapshot(
                conn,
                feed_url=feed.url,
                etag=etag,
                last_modified=modified,
                fetched_at=now,
                item_keys=list(snap.item_keys),
            )
            conn.commit()
            by_url[feed.url] = items_in_window(
                load_items(conn, snap.item_keys),
                window_start=window_start,
                window_end=window_end,
            )
            continue
        items = items or []
        save_items(conn, items, fetched_at=now)
        keys = [item.item_key for item in items]
        save_feed_snapshot(
            conn,
            feed_url=feed.url,
            etag=etag,
            last_modified=modified,
            fetched_at=now,
            item_keys=keys,
        )
        conn.commit()
        by_url[feed.url] = items_in_window(
            items, window_start=window_start, window_end=window_end
        )
    return by_url, failures, logs
