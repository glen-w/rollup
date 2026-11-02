"""Live source-intelligence suggestions. Never mutate Thunderbird or auto-apply."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import combinations

from rollup.attention.profile import jaccard
from rollup.coverage.cluster import TITLE_JACCARD_MIN
from rollup.coverage.normalize import canonical_url, title_tokens
from rollup.source_quality import SourceQualityRow, score_sources
from rollup.utc import parse_utc

UNUSED_DAYS = 56
DISMISS_RATE_MIN = 0.6
DISMISS_POP_MIN = 5
LOW_QUALITY_MAX = 2.5
LOW_SAVE_RATE_MAX = 0.10
LOW_PRIORITY_POP_MIN = 5
OVERLAP_MIN_SHARED = 3
DUPLICATE_RATIO_MIN = 0.5
DUPLICATE_POP_MIN = 3
RECENT_RUNS = 8
MAX_SUGGESTIONS = 50
LOWER_PRIORITY_STEP = 25

SUGGESTION_CODES = (
    "unused_8w",
    "mostly_dismissed",
    "overlapping_coverage",
    "consider_lower_priority",
    "mostly_duplicate",
)


@dataclass(frozen=True)
class SourceSuggestion:
    code: str
    source_key: str
    display_name: str | None
    message: str
    related_source_key: str | None = None
    apply: str | None = None  # disable | lower_priority


def collect_suggestions(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> tuple[SourceSuggestion, ...]:
    """Compute suggestions from Quality + registry + recent entry overlap."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if "sources" not in tables:
        return ()

    disabled, priorities, names = _source_meta(conn)
    if not names:
        return ()

    quality = {
        row.canonical_source_key: row
        for row in score_sources(conn, now=now, limit=10_000, offset=0)
    }
    out: list[SourceSuggestion] = []
    out.extend(_unused_8w(conn, now, disabled, names))
    out.extend(_mostly_dismissed(quality, disabled, names))
    out.extend(_consider_lower_priority(quality, disabled, priorities, names))
    overlap_rows, duplicate_rows = _overlap_and_duplicate(
        conn, quality, disabled, names
    )
    out.extend(overlap_rows)
    out.extend(duplicate_rows)
    # Stable: code then source_key. Cap.
    out.sort(key=lambda s: (SUGGESTION_CODES.index(s.code), s.source_key))
    return tuple(out[:MAX_SUGGESTIONS])


def suggestion_to_dict(item: SourceSuggestion) -> dict:
    return {
        "code": item.code,
        "source_key": item.source_key,
        "display_name": item.display_name,
        "message": item.message,
        "related_source_key": item.related_source_key,
        "apply": item.apply,
    }


def format_suggestions(items: tuple[SourceSuggestion, ...]) -> str:
    if not items:
        return "No source suggestions."
    lines = [f"suggestions ({len(items)})"]
    for item in items:
        label = item.display_name or item.source_key
        extra = f"  related={item.related_source_key}" if item.related_source_key else ""
        apply = f"  apply={item.apply}" if item.apply else ""
        lines.append(f"{item.code}  {label}  {item.message}{extra}{apply}")
    return "\n".join(lines)


def _source_meta(
    conn: sqlite3.Connection,
) -> tuple[set[str], dict[str, int | None], dict[str, str | None]]:
    rows = conn.execute(
        """
        SELECT s.source_key, o.enabled, o.priority,
               COALESCE(o.display_name, s.display_name_observed)
        FROM sources s
        LEFT JOIN source_overrides o ON o.source_key = s.source_key
        """
    ).fetchall()
    disabled: set[str] = set()
    priorities: dict[str, int | None] = {}
    names: dict[str, str | None] = {}
    for key, enabled, priority, name in rows:
        names[key] = name
        priorities[key] = None if priority is None else int(priority)
        if enabled is not None and int(enabled) == 0:
            disabled.add(key)
    return disabled, priorities, names


def _label(key: str, names: dict[str, str | None]) -> str | None:
    return names.get(key)


def _unused_8w(
    conn: sqlite3.Connection,
    now: datetime,
    disabled: set[str],
    names: dict[str, str | None],
) -> list[SourceSuggestion]:
    cutoff = now - timedelta(days=UNUSED_DAYS)
    rows = conn.execute(
        "SELECT source_key, last_seen_at FROM source_observations"
    ).fetchall()
    out: list[SourceSuggestion] = []
    for key, last_seen in rows:
        if key in disabled:
            continue
        dt = parse_utc(last_seen) if last_seen else None
        if dt is not None and dt >= cutoff:
            continue
        out.append(
            SourceSuggestion(
                code="unused_8w",
                source_key=key,
                display_name=_label(key, names),
                message=f"No mail in {UNUSED_DAYS} days",
                apply="disable",
            )
        )
    return out


def _mostly_dismissed(
    quality: dict[str, SourceQualityRow],
    disabled: set[str],
    names: dict[str, str | None],
) -> list[SourceSuggestion]:
    out: list[SourceSuggestion] = []
    for key, row in quality.items():
        if key in disabled:
            continue
        if row.population < DISMISS_POP_MIN or row.dismiss_rate is None:
            continue
        if row.dismiss_rate < DISMISS_RATE_MIN:
            continue
        pct = int(round(row.dismiss_rate * 100))
        out.append(
            SourceSuggestion(
                code="mostly_dismissed",
                source_key=key,
                display_name=_label(key, names),
                message=f"{pct}% dismissed (n={row.population})",
                apply="disable",
            )
        )
    return out


def _consider_lower_priority(
    quality: dict[str, SourceQualityRow],
    disabled: set[str],
    priorities: dict[str, int | None],
    names: dict[str, str | None],
) -> list[SourceSuggestion]:
    out: list[SourceSuggestion] = []
    for key, row in quality.items():
        if key in disabled:
            continue
        if row.population < LOW_PRIORITY_POP_MIN:
            continue
        if row.adjusted_score is None or row.adjusted_score >= LOW_QUALITY_MAX:
            continue
        if row.save_rate is None or row.save_rate >= LOW_SAVE_RATE_MAX:
            continue
        apply = "lower_priority"
        current = priorities.get(key)
        if current is not None and current <= 0:
            apply = None
        out.append(
            SourceSuggestion(
                code="consider_lower_priority",
                source_key=key,
                display_name=_label(key, names),
                message=(
                    f"Quality {row.adjusted_score:.1f} and "
                    f"{int(round(row.save_rate * 100))}% saved (n={row.population})"
                ),
                apply=apply,
            )
        )
    return out


def _overlap_and_duplicate(
    conn: sqlite3.Connection,
    quality: dict[str, SourceQualityRow],
    disabled: set[str],
    names: dict[str, str | None],
) -> tuple[list[SourceSuggestion], list[SourceSuggestion]]:
    items_by_source, pair_counts = _recent_overlap(conn)
    overlap: list[SourceSuggestion] = []
    seen_overlap: set[str] = set()
    for pair, n in pair_counts.items():
        if n < OVERLAP_MIN_SHARED:
            continue
        a, b = tuple(pair)
        if a not in names or b not in names:
            continue
        if a in disabled or b in disabled:
            continue
        for key, other in ((a, b), (b, a)):
            if key in seen_overlap:
                continue
            seen_overlap.add(key)
            overlap.append(
                SourceSuggestion(
                    code="overlapping_coverage",
                    source_key=key,
                    display_name=_label(key, names),
                    message=f"Covered with {other} ({n} overlapping items)",
                    related_source_key=other,
                    apply="lower_priority",
                )
            )

    duplicate: list[SourceSuggestion] = []
    for key, items in items_by_source.items():
        if key not in names or key in disabled or len(items) < DUPLICATE_POP_MIN:
            continue
        best_other = None
        best_ratio = 0.0
        own_score = _quality_rank(quality.get(key))
        for other, other_items in items_by_source.items():
            if other == key or other in disabled:
                continue
            if _quality_rank(quality.get(other)) <= own_score:
                continue
            matched = sum(
                1 for item in items if _matches_any(item, other_items)
            )
            ratio = matched / len(items)
            if ratio > best_ratio:
                best_ratio = ratio
                best_other = other
        if best_other is None or best_ratio < DUPLICATE_RATIO_MIN:
            continue
        pct = int(round(best_ratio * 100))
        duplicate.append(
            SourceSuggestion(
                code="mostly_duplicate",
                source_key=key,
                display_name=_label(key, names),
                message=f"{pct}% overlap with higher-quality {best_other}",
                related_source_key=best_other,
                apply="disable",
            )
        )
    return overlap, duplicate


def _quality_rank(row: SourceQualityRow | None) -> float:
    if row is None or row.adjusted_score is None:
        return float("-inf")
    return float(row.adjusted_score)


@dataclass(frozen=True)
class _Item:
    source: str
    tokens: frozenset[str]
    url: str | None


def _recent_overlap(
    conn: sqlite3.Connection,
) -> tuple[dict[str, list[_Item]], dict[frozenset[str], int]]:
    runs = conn.execute(
        """SELECT run_id FROM rollup_runs
           ORDER BY started_at DESC, run_id DESC LIMIT ?""",
        (RECENT_RUNS,),
    ).fetchall()
    run_ids = [r[0] for r in runs]
    items_by_source: dict[str, list[_Item]] = defaultdict(list)
    pair_counts: dict[frozenset[str], int] = defaultdict(int)
    if not run_ids:
        return items_by_source, pair_counts
    placeholders = ",".join("?" * len(run_ids))
    rows = conn.execute(
        f"""SELECT run_id, source_key_observed, subject, primary_link
            FROM rollup_entries
            WHERE run_id IN ({placeholders})
              AND source_key_observed IS NOT NULL""",
        run_ids,
    ).fetchall()
    by_run: dict[str, list[_Item]] = defaultdict(list)
    for run_id, source, subject, link in rows:
        item = _Item(
            source=str(source),
            tokens=title_tokens(subject or ""),
            url=canonical_url(link or "") if link else None,
        )
        items_by_source[item.source].append(item)
        by_run[str(run_id)].append(item)
    for run_items in by_run.values():
        for a, b in combinations(run_items, 2):
            if a.source == b.source:
                continue
            if _items_overlap(a, b):
                pair_counts[frozenset({a.source, b.source})] += 1
    return items_by_source, pair_counts


def _items_overlap(a: _Item, b: _Item) -> bool:
    if a.url and b.url and a.url == b.url:
        return True
    if a.tokens and b.tokens and jaccard(a.tokens, b.tokens) >= TITLE_JACCARD_MIN:
        return True
    return False


def _matches_any(item: _Item, others: list[_Item]) -> bool:
    return any(_items_overlap(item, other) for other in others)
