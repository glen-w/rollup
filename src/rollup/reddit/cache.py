"""SQLite listing cache for Reddit subs."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from rollup.reddit.config import RedditConfig, RedditSub, lookback_to_time_filter
from rollup.reddit.fetch import fetch_posts_for_subs
from rollup.reddit.models import RedditPost
from rollup.reddit.session import rss_sort_path
from rollup.source_fetch_cache import format_cache_age, parse_iso_datetime, snapshot_is_fresh

_TIME_FILTER_NULL = ""


@dataclass(frozen=True)
class RedditListingSnapshot:
    subreddit: str
    sort: str
    time_filter: str | None
    fetched_at: datetime
    post_ids: tuple[str, ...]


def _time_filter_key(time_filter: str | None) -> str:
    return time_filter or _TIME_FILTER_NULL


def _resolved_time_filter(
    sub: RedditSub,
    config: RedditConfig,
    lookback_days: int,
) -> str | None:
    sort = sub.resolved_sort(config.sort)
    if rss_sort_path(sort) != "top":
        return None
    return config.time_filter or lookback_to_time_filter(lookback_days)


def _post_to_row(post: RedditPost, fetched_at: datetime) -> tuple:
    return (
        post.post_id,
        post.subreddit,
        post.title,
        post.selftext,
        post.author,
        post.permalink,
        post.url,
        int(post.score),
        int(post.num_comments),
        post.created_at.isoformat() if post.created_at else None,
        int(post.over_18),
        int(post.is_self),
        fetched_at.isoformat(),
    )


def _row_to_post(row: tuple) -> RedditPost:
    created = parse_iso_datetime(row[9])
    return RedditPost(
        post_id=row[0],
        subreddit=row[1],
        title=row[2],
        selftext=row[3],
        author=row[4],
        permalink=row[5],
        url=row[6],
        score=int(row[7]),
        num_comments=int(row[8]),
        created_at=created,
        over_18=bool(row[10]),
        is_self=bool(row[11]),
    )


def get_listing_snapshot(
    conn: sqlite3.Connection,
    *,
    subreddit: str,
    sort: str,
    time_filter: str | None,
) -> RedditListingSnapshot | None:
    row = conn.execute(
        """
        SELECT subreddit, sort, time_filter, fetched_at, post_ids_json
        FROM reddit_listing_snapshots
        WHERE subreddit = ? AND sort = ? AND time_filter = ?
        """,
        (subreddit, sort, _time_filter_key(time_filter)),
    ).fetchone()
    if not row:
        return None
    tf = row[2] or None
    if tf == _TIME_FILTER_NULL:
        tf = None
    fetched_at = parse_iso_datetime(row[3])
    if fetched_at is None:
        return None
    ids = tuple(json.loads(row[4]))
    return RedditListingSnapshot(
        subreddit=row[0],
        sort=row[1],
        time_filter=tf,
        fetched_at=fetched_at,
        post_ids=ids,
    )


def load_posts_by_ids(
    conn: sqlite3.Connection,
    post_ids: tuple[str, ...],
) -> list[RedditPost]:
    if not post_ids:
        return []
    placeholders = ",".join("?" for _ in post_ids)
    rows = conn.execute(
        f"""
        SELECT post_id, subreddit, title, selftext, author, permalink, url,
               score, num_comments, created_at, over_18, is_self
        FROM reddit_posts
        WHERE post_id IN ({placeholders})
        """,
        post_ids,
    ).fetchall()
    by_id = {row[0]: _row_to_post(row) for row in rows}
    return [by_id[pid] for pid in post_ids if pid in by_id]


def _filter_posts_window(
    posts: list[RedditPost],
    *,
    window_start: datetime | None,
    window_end: datetime | None,
) -> list[RedditPost]:
    if window_start is None or window_end is None:
        return posts
    filtered: list[RedditPost] = []
    for post in posts:
        if post.created_at is None:
            continue
        if window_start <= post.created_at <= window_end:
            filtered.append(post)
    return filtered


def load_cached_sub_posts(
    conn: sqlite3.Connection,
    snapshot: RedditListingSnapshot,
    *,
    limit: int,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[RedditPost]:
    ids = snapshot.post_ids[:limit]
    posts = load_posts_by_ids(conn, ids)
    return _filter_posts_window(posts, window_start=window_start, window_end=window_end)


def save_listing_snapshot(
    conn: sqlite3.Connection,
    *,
    sub: RedditSub,
    config: RedditConfig,
    lookback_days: int,
    posts: list[RedditPost],
    fetched_at: datetime,
) -> None:
    sort = sub.resolved_sort(config.sort)
    time_filter = _resolved_time_filter(sub, config, lookback_days)
    post_ids = [post.post_id for post in posts]
    for post in posts:
        conn.execute(
            """
            INSERT INTO reddit_posts (
                post_id, subreddit, title, selftext, author, permalink, url,
                score, num_comments, created_at, over_18, is_self, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_id) DO UPDATE SET
                subreddit = excluded.subreddit,
                title = excluded.title,
                selftext = excluded.selftext,
                author = excluded.author,
                permalink = excluded.permalink,
                url = excluded.url,
                score = excluded.score,
                num_comments = excluded.num_comments,
                created_at = excluded.created_at,
                over_18 = excluded.over_18,
                is_self = excluded.is_self,
                fetched_at = excluded.fetched_at
            """,
            _post_to_row(post, fetched_at),
        )
    conn.execute(
        """
        INSERT INTO reddit_listing_snapshots (
            subreddit, sort, time_filter, fetched_at, post_ids_json
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(subreddit, sort, time_filter) DO UPDATE SET
            fetched_at = excluded.fetched_at,
            post_ids_json = excluded.post_ids_json
        """,
        (
            sub.name,
            sort,
            _time_filter_key(time_filter),
            fetched_at.isoformat(),
            json.dumps(post_ids),
        ),
    )
    conn.commit()


def should_fetch_sub(
    conn: sqlite3.Connection,
    *,
    sub: RedditSub,
    config: RedditConfig,
    lookback_days: int,
    ttl_hours: int,
    refresh: bool,
    now: datetime,
) -> tuple[bool, RedditListingSnapshot | None]:
    sort = sub.resolved_sort(config.sort)
    time_filter = _resolved_time_filter(sub, config, lookback_days)
    snapshot = get_listing_snapshot(
        conn,
        subreddit=sub.name,
        sort=sort,
        time_filter=time_filter,
    )
    if refresh or ttl_hours <= 0:
        return True, snapshot
    if snapshot is None:
        return True, None
    if not snapshot_is_fresh(snapshot.fetched_at, ttl_hours, now):
        return True, snapshot
    limit = sub.resolved_limit(config.limit)
    if len(snapshot.post_ids) < limit:
        return True, snapshot
    return False, snapshot


def partition_reddit_subs(
    conn: sqlite3.Connection,
    subs: tuple[RedditSub, ...],
    *,
    config: RedditConfig,
    lookback_days: int,
    ttl_hours: int,
    refresh: bool,
    now: datetime,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> tuple[dict[str, list[RedditPost]], list[RedditSub], list[str]]:
    """Return cached posts, subs needing network fetch, and cache-hit log lines."""
    cached: dict[str, list[RedditPost]] = {}
    to_fetch: list[RedditSub] = []
    log_lines: list[str] = []
    for sub in subs:
        need_fetch, snapshot = should_fetch_sub(
            conn,
            sub=sub,
            config=config,
            lookback_days=lookback_days,
            ttl_hours=ttl_hours,
            refresh=refresh,
            now=now,
        )
        if need_fetch:
            to_fetch.append(sub)
            continue
        assert snapshot is not None
        limit = sub.resolved_limit(config.limit)
        posts = load_cached_sub_posts(
            conn,
            snapshot,
            limit=limit,
            window_start=window_start,
            window_end=window_end,
        )
        cached[sub.name] = posts
        log_lines.append(
            f"Reddit cache hit r/{sub.name} ({format_cache_age(snapshot.fetched_at, now)})"
        )
    return cached, to_fetch, log_lines


def count_subs_needing_fetch(
    conn: sqlite3.Connection,
    subs: tuple[RedditSub, ...],
    *,
    config: RedditConfig,
    lookback_days: int,
    ttl_hours: int,
    refresh: bool,
    now: datetime,
) -> int:
    return sum(
        1
        for sub in subs
        if should_fetch_sub(
            conn,
            sub=sub,
            config=config,
            lookback_days=lookback_days,
            ttl_hours=ttl_hours,
            refresh=refresh,
            now=now,
        )[0]
    )


def fetch_and_cache_reddit_subs(
    conn: sqlite3.Connection,
    subs: tuple[RedditSub, ...],
    *,
    config: RedditConfig,
    lookback_days: int,
    window_start: datetime | None,
    window_end: datetime | None,
    fetched_at: datetime,
    client=None,
) -> tuple[dict[str, list[RedditPost]], list[str], list[tuple[str, RedditListingSnapshot | None]]]:
    """Fetch subs over the network, persist snapshots, return posts and failures."""
    posts_by_sub, failures = fetch_posts_for_subs(
        subs,
        config=config,
        lookback_days=lookback_days,
        client=client,
        window_start=window_start,
        window_end=window_end,
    )
    for sub_name, posts in posts_by_sub.items():
        sub = next(s for s in subs if s.name == sub_name)
        save_listing_snapshot(
            conn,
            sub=sub,
            config=config,
            lookback_days=lookback_days,
            posts=posts,
            fetched_at=fetched_at,
        )
    stale_candidates: list[tuple[str, RedditListingSnapshot | None]] = []
    for failure in failures:
        if failure.startswith("r/"):
            sub_name = failure.split(":", 1)[0][2:]
        else:
            continue
        sub = next((s for s in subs if s.name == sub_name), None)
        if sub is None:
            continue
        sort = sub.resolved_sort(config.sort)
        time_filter = _resolved_time_filter(sub, config, lookback_days)
        snapshot = get_listing_snapshot(
            conn,
            subreddit=sub.name,
            sort=sort,
            time_filter=time_filter,
        )
        stale_candidates.append((sub_name, snapshot))
    return posts_by_sub, failures, stale_candidates


def resolve_stale_sub_posts(
    conn: sqlite3.Connection,
    sub: RedditSub,
    config: RedditConfig,
    lookback_days: int,
    snapshot: RedditListingSnapshot,
    *,
    window_start: datetime | None,
    window_end: datetime | None,
) -> list[RedditPost]:
    limit = sub.resolved_limit(config.limit)
    return load_cached_sub_posts(
        conn,
        snapshot,
        limit=limit,
        window_start=window_start,
        window_end=window_end,
    )
