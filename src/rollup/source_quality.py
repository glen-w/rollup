"""Canonical newsletter quality scoring (Bayesian + recent weighting)."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from rollup.source_registry import resolve_alias
from rollup.utc import parse_utc

PRIOR_WEIGHT = 3.0
DEFAULT_PRIOR = 3.0
RECENT_DAYS = 90
HALF_LIFE_DAYS = 30.0
TREND_THRESHOLD = 0.15
SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class SourceQualityRow:
    canonical_source_key: str
    display_name: str | None
    rating_count: int
    lifetime_average: float | None
    recent_weighted: float | None
    adjusted_score: float | None
    trend: str  # up | down | flat | unknown
    last_rated_at: str | None
    last_received_at: str | None
    population: int
    read_rate: float | None
    save_rate: float | None
    dismiss_rate: float | None


def bayesian_adjusted(n: int, mean: float, prior: float, c: float = PRIOR_WEIGHT) -> float:
    return (n * mean + c * prior) / (n + c)


def recent_weight(age_days: float, half_life: float = HALF_LIFE_DAYS) -> float:
    return 0.5 ** (age_days / half_life)


def _in_recent_window(age_seconds: float) -> bool:
    """Include age exactly at 90 days; exclude negative (future) ages."""
    return 0.0 <= age_seconds <= RECENT_DAYS * SECONDS_PER_DAY


def global_prior_mean_of_source_means(
    source_means: Iterable[float],
) -> float:
    values = list(source_means)
    if not values:
        return DEFAULT_PRIOR
    return sum(values) / len(values)


def _load_alias_map(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT alias_key, canonical_source_key FROM source_aliases"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _canonical(key: str, aliases: dict[str, str]) -> str:
    seen: set[str] = set()
    cur = key
    while cur in aliases and cur not in seen:
        seen.add(cur)
        cur = aliases[cur]
    return cur


def score_sources(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    limit: int = 200,
    offset: int = 0,
) -> list[SourceQualityRow]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    aliases = _load_alias_map(conn)

    # Ratings joined to observed source links.
    rating_rows = conn.execute(
        """SELECT r.message_key, r.stars, r.updated_at, l.source_key_observed
           FROM message_ratings r
           JOIN message_source_links l ON l.message_key = r.message_key"""
    ).fetchall()

    by_canonical: dict[str, list[tuple[int, datetime]]] = {}
    for _mk, stars, updated_at, observed in rating_rows:
        can = _canonical(observed, aliases)
        dt = parse_utc(updated_at)
        if dt is None:
            continue
        by_canonical.setdefault(can, []).append((int(stars), dt))

    source_means = [
        sum(s for s, _ in vals) / len(vals) for vals in by_canonical.values() if vals
    ]
    prior = global_prior_mean_of_source_means(source_means)

    # Population: indexed messages attributed to each canonical source.
    pop_rows = conn.execute(
        """SELECT l.source_key_observed, l.message_key
           FROM message_source_links l
           WHERE EXISTS (
             SELECT 1 FROM rollup_entries e WHERE e.message_key = l.message_key
           )"""
    ).fetchall()
    population: dict[str, set[str]] = {}
    for observed, mk in pop_rows:
        can = _canonical(observed, aliases)
        population.setdefault(can, set()).add(mk)

    interaction = {
        r[0]: (r[1], r[2], r[3])
        for r in conn.execute(
            "SELECT message_key, read_at, saved_at, dismissed_at FROM message_interaction"
        )
    }

    last_seen = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT source_key, last_seen_at FROM source_observations"
        )
    }
    display_names = {
        r[0]: r[1]
        for r in conn.execute(
            """SELECT s.source_key,
                      COALESCE(o.display_name, s.display_name_observed)
               FROM sources s
               LEFT JOIN source_overrides o ON o.source_key = s.source_key"""
        )
    }

    # Include unrated sources that appear in observations/population.
    all_keys = set(by_canonical) | set(population) | set(last_seen)

    rows: list[SourceQualityRow] = []
    for can in all_keys:
        ratings = by_canonical.get(can, [])
        n = len(ratings)
        lifetime = None
        recent = None
        adjusted = None
        trend = "unknown"
        last_rated = None
        if n:
            lifetime = sum(s for s, _ in ratings) / n
            adjusted = bayesian_adjusted(n, lifetime, prior)
            last_rated = max(dt for _, dt in ratings)
            weighted_num = 0.0
            weighted_den = 0.0
            for stars, dt in ratings:
                age = (now - dt).total_seconds()
                if not _in_recent_window(age):
                    continue
                w = recent_weight(age / SECONDS_PER_DAY)
                weighted_num += w * stars
                weighted_den += w
            if weighted_den > 0:
                recent = weighted_num / weighted_den
                delta = recent - lifetime
                if delta > TREND_THRESHOLD:
                    trend = "up"
                elif delta < -TREND_THRESHOLD:
                    trend = "down"
                else:
                    trend = "flat"
            last_rated_s = last_rated.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            last_rated_s = None

        pop_keys = population.get(can, set())
        pop_n = len(pop_keys)
        read_n = save_n = dismiss_n = 0
        for mk in pop_keys:
            inter = interaction.get(mk)
            if not inter:
                continue
            if inter[0]:
                read_n += 1
            if inter[1]:
                save_n += 1
            if inter[2]:
                dismiss_n += 1

        def rate(num: int) -> float | None:
            if pop_n == 0:
                return None
            return num / pop_n

        # last_received: prefer observation, else max from entries
        last_recv = last_seen.get(can)
        rows.append(
            SourceQualityRow(
                canonical_source_key=can,
                display_name=display_names.get(can),
                rating_count=n,
                lifetime_average=lifetime,
                recent_weighted=recent,
                adjusted_score=adjusted,
                trend=trend,
                last_rated_at=last_rated_s,
                last_received_at=last_recv,
                population=pop_n,
                read_rate=rate(read_n),
                save_rate=rate(save_n),
                dismiss_rate=rate(dismiss_n),
            )
        )

    def sort_key(r: SourceQualityRow) -> tuple:
        rated = 0 if r.adjusted_score is not None else 1
        return (
            rated,
            -(r.adjusted_score if r.adjusted_score is not None else 0.0),
            -r.rating_count,
            # Empty last_rated sorts after real timestamps for DESC: use "" with reverse
            # Trick: sort by (-has_date, date DESC via reverse string compare using negation)
            0 if r.last_rated_at else 1,
            # For ISO dates, reverse lexicographic = reverse chronological if we negate via
            # sorting secondary key descending — use a pair
            r.last_rated_at or "",
            r.canonical_source_key,
        )

    # Stable multi-pass: primary keys then last_rated DESC among ties
    rows.sort(key=lambda r: r.canonical_source_key)
    rows.sort(key=lambda r: r.last_rated_at or "", reverse=True)
    rows.sort(key=lambda r: r.rating_count, reverse=True)
    rows.sort(
        key=lambda r: r.adjusted_score if r.adjusted_score is not None else float("-inf"),
        reverse=True,
    )
    rows.sort(key=lambda r: 0 if r.adjusted_score is not None else 1)
    return rows[offset : offset + limit]
