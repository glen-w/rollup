"""Load Quality / interaction / source-priority signals for attention scoring."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from rollup.attention.score import AttentionSignals
from rollup.source_quality import score_sources


def load_attention_signals(
    conn: sqlite3.Connection | None,
    snapshot=None,
    *,
    now: datetime | None = None,
) -> AttentionSignals:
    quality: dict[str, float] = {}
    saved: set[str] = set()
    dismissed: set[str] = set()
    read: set[str] = set()
    if conn is not None:
        when = now or datetime.now(timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        else:
            when = when.astimezone(timezone.utc)
        try:
            for row in score_sources(conn, now=when, limit=10_000):
                if row.adjusted_score is not None:
                    quality[row.canonical_source_key] = float(row.adjusted_score)
        except sqlite3.Error:
            quality = {}
        try:
            for key, read_at, saved_at, dismissed_at in conn.execute(
                "SELECT message_key, read_at, saved_at, dismissed_at "
                "FROM message_interaction"
            ):
                if read_at:
                    read.add(key)
                if saved_at:
                    saved.add(key)
                if dismissed_at:
                    dismissed.add(key)
        except sqlite3.Error:
            pass
    priority: dict[str, int] = {}
    if snapshot is not None:
        for source_key, policy in dict(snapshot.policies or {}).items():
            priority[source_key] = int(getattr(policy, "priority", 0) or 0)
        for alias, canonical in dict(snapshot.aliases or {}).items():
            if canonical in quality:
                quality[alias] = quality[canonical]
            if canonical in priority:
                priority[alias] = priority[canonical]
            else:
                policy = snapshot.policy_for(alias)
                if policy is not None:
                    priority[alias] = int(policy.priority or 0)
    return AttentionSignals(
        quality_by_source=quality,
        priority_by_source=priority,
        saved=frozenset(saved),
        dismissed=frozenset(dismissed),
        read=frozenset(read),
    )
