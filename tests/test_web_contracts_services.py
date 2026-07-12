"""Expanded ratings, interaction, and quality contract tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rollup.interaction import (
    dismiss,
    get_interaction,
    mark_read,
    save,
    undismiss,
    unsave,
)
from rollup.ratings import RatingError, get_rating, set_rating_with_reasons
from rollup.source_quality import (
    PRIOR_WEIGHT,
    bayesian_adjusted,
    score_sources,
)
from rollup.source_registry import alias_sources, ensure_source_anchor
from rollup.state import init_db
from rollup.utc import format_utc


NOW = datetime(2024, 7, 1, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path: Path):
    c = init_db(tmp_path / "rollup.db")
    yield c
    c.close()


def test_star_three_accepts_either_polarity(conn):
    set_rating_with_reasons(conn, "mid:a@x", 3, ["concise", "too_long"], now=NOW)
    r = get_rating(conn, "mid:a@x")
    assert set(r.reason_codes) == {"concise", "too_long"}


def test_inactive_reason_rejected(conn):
    conn.execute(
        "UPDATE rating_reason_codes SET active = 0 WHERE code = 'concise'"
    )
    conn.commit()
    with pytest.raises(RatingError, match="inactive"):
        set_rating_with_reasons(conn, "mid:a@x", 5, ["concise"], now=NOW)


def test_unsave_and_undismiss(conn):
    key = "mid:a@x"
    save(conn, key, now=NOW)
    dismiss(conn, key, now=NOW)
    unsave(conn, key, now=NOW)
    undismiss(conn, key, now=NOW)
    st = get_interaction(conn, key)
    assert st.is_read and not st.is_saved and not st.is_dismissed


def test_save_sets_read(conn):
    key = "mid:b@x"
    save(conn, key, now=NOW)
    assert get_interaction(conn, key).is_read


def _seed_source(conn, key: str, name: str) -> None:
    ensure_source_anchor(conn, key, now=NOW, display_name_observed=name)
    conn.commit()


def _seed_link_and_entry(conn, message_key: str, source_key: str, run_id: str, pos: int):
    iso = format_utc(NOW)
    conn.execute(
        """INSERT OR IGNORE INTO rollup_runs (
            run_id, started_at, status, entry_index_version, stats_completeness,
            index_source, indexed_at
           ) VALUES (?, ?, 'success', 1, 'full', 'pipeline', ?)""",
        (run_id, iso, iso),
    )
    conn.execute(
        """INSERT OR REPLACE INTO message_source_links
           (message_key, source_key_observed, updated_at) VALUES (?, ?, ?)""",
        (message_key, source_key, iso),
    )
    conn.execute(
        """INSERT OR REPLACE INTO rollup_entries (
            run_id, message_key, source_key_observed, section_position,
            entry_position, display_position, links_json
           ) VALUES (?, ?, ?, 0, 0, ?, '[]')""",
        (run_id, message_key, source_key, pos),
    )
    conn.commit()


def test_alias_aggregation_and_mean_of_means_prior(conn):
    run_id = "550e8400-e29b-41d4-a716-446655440000"
    _seed_source(conn, "from:a@ex.com", "A")
    _seed_source(conn, "from:b@ex.com", "B")
    _seed_source(conn, "from:old@ex.com", "Old")
    alias_sources(conn, "from:old@ex.com", "from:a@ex.com", now=NOW)
    _seed_link_and_entry(conn, "mid:1@x", "from:a@ex.com", run_id, 0)
    _seed_link_and_entry(conn, "mid:2@x", "from:old@ex.com", run_id, 1)
    _seed_link_and_entry(conn, "mid:3@x", "from:b@ex.com", run_id, 2)
    # A gets two ratings via canonical+alias; B gets one low rating
    set_rating_with_reasons(conn, "mid:1@x", 5, [], now=NOW)
    set_rating_with_reasons(conn, "mid:2@x", 5, [], now=NOW)
    set_rating_with_reasons(conn, "mid:3@x", 1, ["not_relevant"], now=NOW)

    rows = {r.canonical_source_key: r for r in score_sources(conn, now=NOW)}
    assert "from:old@ex.com" not in rows  # aggregated away
    assert rows["from:a@ex.com"].rating_count == 2
    # prior = mean of source means = (5 + 1) / 2 = 3, not message-mean (11/3)
    prior = 3.0
    expected = bayesian_adjusted(2, 5.0, prior, PRIOR_WEIGHT)
    assert abs(rows["from:a@ex.com"].adjusted_score - expected) < 1e-9


def test_list_prefers_over_from_on_link_conflict(conn):
    from rollup.run_index import _prefer_source_key, _upsert_message_source_link

    iso = format_utc(NOW)
    _upsert_message_source_link(conn, "mid:c@x", "from:a@ex.com", iso)
    _upsert_message_source_link(conn, "mid:c@x", "list:news.ex.com", iso)
    conn.commit()
    row = conn.execute(
        "SELECT source_key_observed FROM message_source_links WHERE message_key=?",
        ("mid:c@x",),
    ).fetchone()[0]
    assert row == "list:news.ex.com"
    assert _prefer_source_key("from:a@ex.com", "list:news.ex.com") == "list:news.ex.com"


def test_rate_denominators_and_injected_now(conn):
    run_id = "550e8400-e29b-41d4-a716-446655440001"
    _seed_source(conn, "from:a@ex.com", "A")
    _seed_link_and_entry(conn, "mid:1@x", "from:a@ex.com", run_id, 0)
    _seed_link_and_entry(conn, "mid:2@x", "from:a@ex.com", run_id, 1)
    mark_read(conn, "mid:1@x", now=NOW)
    # mid:2 unread (no row)
    old = NOW - timedelta(days=100)
    set_rating_with_reasons(conn, "mid:1@x", 4, [], now=old)
    set_rating_with_reasons(
        conn, "mid:2@x", 5, [], now=NOW + timedelta(days=1)
    )  # future

    rows = score_sources(conn, now=NOW)
    a = next(r for r in rows if r.canonical_source_key == "from:a@ex.com")
    assert a.population == 2
    assert a.read_rate == 0.5
    assert a.rating_count == 2
    # future rating excluded from recent; old beyond 90d excluded → recent unknown
    assert a.recent_weighted is None
    assert a.trend == "unknown"


def test_exactly_90_day_rating_included_in_recent(conn):
    run_id = "550e8400-e29b-41d4-a716-446655440002"
    _seed_source(conn, "from:a@ex.com", "A")
    _seed_link_and_entry(conn, "mid:1@x", "from:a@ex.com", run_id, 0)
    aged = NOW - timedelta(days=90)
    set_rating_with_reasons(conn, "mid:1@x", 5, [], now=aged)
    a = next(
        r
        for r in score_sources(conn, now=NOW)
        if r.canonical_source_key == "from:a@ex.com"
    )
    assert a.recent_weighted is not None
    assert abs(a.recent_weighted - 5.0) < 1e-9
