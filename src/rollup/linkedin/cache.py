"""SQLite listing and article-body cache for LinkedIn searches."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from rollup.linkedin.config import LinkedInSearch
from rollup.linkedin.models import LinkedInPost
from rollup.linkedin.parse import linkedin_message_key
from rollup.source_fetch_cache import format_cache_age, parse_iso_datetime, snapshot_is_fresh
from rollup.webpage.url import url_hash


@dataclass(frozen=True)
class LinkedInListingSnapshot:
    slug: str
    url: str
    fetched_at: datetime
    post_keys: tuple[str, ...]


def _post_message_key(post: LinkedInPost) -> str:
    key, _warnings = linkedin_message_key(post)
    return key


def _post_to_row(post: LinkedInPost, fetched_at: datetime) -> tuple:
    message_key = _post_message_key(post)
    return (
        message_key,
        post.activity_id,
        post.author_name,
        post.author_member_id,
        post.text,
        post.permalink,
        post.created_at.isoformat() if post.created_at else None,
        post.article_url,
        post.article_title,
        fetched_at.isoformat(),
    )


def _row_to_post(row: tuple) -> LinkedInPost:
    created = parse_iso_datetime(row[6])
    return LinkedInPost(
        activity_id=row[1],
        author_name=row[2],
        author_member_id=row[3],
        text=row[4],
        permalink=row[5],
        created_at=created,
        article_url=row[7],
        article_title=row[8],
    )


def get_listing_snapshot(
    conn: sqlite3.Connection,
    slug: str,
) -> LinkedInListingSnapshot | None:
    row = conn.execute(
        """
        SELECT slug, url, fetched_at, post_keys_json
        FROM linkedin_listing_snapshots
        WHERE slug = ?
        """,
        (slug,),
    ).fetchone()
    if not row:
        return None
    fetched_at = parse_iso_datetime(row[2])
    if fetched_at is None:
        return None
    keys = tuple(json.loads(row[3]))
    return LinkedInListingSnapshot(
        slug=row[0],
        url=row[1],
        fetched_at=fetched_at,
        post_keys=keys,
    )


def load_posts_by_keys(
    conn: sqlite3.Connection,
    message_keys: tuple[str, ...],
) -> list[LinkedInPost]:
    if not message_keys:
        return []
    placeholders = ",".join("?" for _ in message_keys)
    rows = conn.execute(
        f"""
        SELECT message_key, activity_id, author_name, author_member_id, text,
               permalink, created_at, article_url, article_title
        FROM linkedin_posts
        WHERE message_key IN ({placeholders})
        """,
        message_keys,
    ).fetchall()
    by_key = {row[0]: _row_to_post(row) for row in rows}
    return [by_key[key] for key in message_keys if key in by_key]


def save_listing_snapshot(
    conn: sqlite3.Connection,
    *,
    search: LinkedInSearch,
    posts: list[LinkedInPost],
    fetched_at: datetime,
) -> None:
    post_keys = [_post_message_key(post) for post in posts]
    for post in posts:
        conn.execute(
            """
            INSERT INTO linkedin_posts (
                message_key, activity_id, author_name, author_member_id, text,
                permalink, created_at, article_url, article_title, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_key) DO UPDATE SET
                activity_id = excluded.activity_id,
                author_name = excluded.author_name,
                author_member_id = excluded.author_member_id,
                text = excluded.text,
                permalink = excluded.permalink,
                created_at = excluded.created_at,
                article_url = excluded.article_url,
                article_title = excluded.article_title,
                fetched_at = excluded.fetched_at
            """,
            _post_to_row(post, fetched_at),
        )
    conn.execute(
        """
        INSERT INTO linkedin_listing_snapshots (slug, url, fetched_at, post_keys_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            url = excluded.url,
            fetched_at = excluded.fetched_at,
            post_keys_json = excluded.post_keys_json
        """,
        (
            search.slug,
            search.url,
            fetched_at.isoformat(),
            json.dumps(post_keys),
        ),
    )
    conn.commit()


def get_article_body(conn: sqlite3.Connection, article_url: str) -> str | None:
    digest = url_hash(article_url)
    row = conn.execute(
        "SELECT body_text FROM linkedin_article_bodies WHERE url_hash = ?",
        (digest,),
    ).fetchone()
    if not row:
        return None
    return row[0]


def store_article_body(
    conn: sqlite3.Connection,
    article_url: str,
    body_text: str,
    *,
    fetched_at: datetime,
) -> None:
    digest = url_hash(article_url)
    conn.execute(
        """
        INSERT INTO linkedin_article_bodies (url_hash, url, body_text, fetched_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(url_hash) DO UPDATE SET
            url = excluded.url,
            body_text = excluded.body_text,
            fetched_at = excluded.fetched_at
        """,
        (digest, article_url, body_text, fetched_at.isoformat()),
    )
    conn.commit()


def should_fetch_search(
    conn: sqlite3.Connection,
    *,
    search: LinkedInSearch,
    ttl_hours: int,
    refresh: bool,
    now: datetime,
) -> tuple[bool, LinkedInListingSnapshot | None]:
    snapshot = get_listing_snapshot(conn, search.slug)
    if refresh or ttl_hours <= 0:
        return True, snapshot
    if snapshot is None or snapshot.url != search.url:
        return True, snapshot
    if not snapshot_is_fresh(snapshot.fetched_at, ttl_hours, now):
        return True, snapshot
    return False, snapshot


def partition_linkedin_searches(
    conn: sqlite3.Connection,
    searches: tuple[LinkedInSearch, ...],
    *,
    ttl_hours: int,
    refresh: bool,
    now: datetime,
) -> tuple[dict[str, list[LinkedInPost]], list[LinkedInSearch], list[str]]:
    cached: dict[str, list[LinkedInPost]] = {}
    to_fetch: list[LinkedInSearch] = []
    log_lines: list[str] = []
    for search in searches:
        need_fetch, snapshot = should_fetch_search(
            conn,
            search=search,
            ttl_hours=ttl_hours,
            refresh=refresh,
            now=now,
        )
        if need_fetch:
            to_fetch.append(search)
            continue
        assert snapshot is not None
        posts = load_posts_by_keys(conn, snapshot.post_keys)
        cached[search.slug] = posts
        log_lines.append(
            f"LinkedIn cache hit {search.slug} ({format_cache_age(snapshot.fetched_at, now)})"
        )
    return cached, to_fetch, log_lines


def count_searches_needing_fetch(
    conn: sqlite3.Connection,
    searches: tuple[LinkedInSearch, ...],
    *,
    ttl_hours: int,
    refresh: bool,
    now: datetime,
) -> int:
    return sum(
        1
        for search in searches
        if should_fetch_search(
            conn,
            search=search,
            ttl_hours=ttl_hours,
            refresh=refresh,
            now=now,
        )[0]
    )
