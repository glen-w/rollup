"""Shared TTL helpers for persisted network-source listing caches."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

DEFAULT_FETCH_TTL_HOURS = 24
MAX_FETCH_TTL_HOURS = 168


def parse_fetch_ttl_hours(value: object, *, path: str, context: str) -> int:
    if value is None:
        return DEFAULT_FETCH_TTL_HOURS
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{path}: {context} must be an integer")
    if value < 0 or value > MAX_FETCH_TTL_HOURS:
        raise ValueError(
            f"{path}: {context} must be between 0 and {MAX_FETCH_TTL_HOURS}"
        )
    return value


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def snapshot_is_fresh(
    fetched_at: datetime,
    ttl_hours: int,
    now: datetime,
) -> bool:
    """Return True when *fetched_at* is within *ttl_hours* of *now*."""
    if ttl_hours <= 0:
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - fetched_at) < timedelta(hours=ttl_hours)


def format_cache_age(fetched_at: datetime, now: datetime) -> str:
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    seconds = max(0, int((now - fetched_at).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"
