"""Source quality scoring tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from rollup.ratings import set_rating_with_reasons
from rollup.source_quality import (
    PRIOR_WEIGHT,
    bayesian_adjusted,
    global_prior_mean_of_source_means,
    recent_weight,
    score_sources,
)
from rollup.state import init_db
from rollup.utc import format_utc


def test_bayesian_and_prior():
    assert bayesian_adjusted(0, 0.0, 3.0) == 3.0
    assert abs(bayesian_adjusted(2, 5.0, 3.0) - (10 + 9) / 5) < 1e-9
    assert global_prior_mean_of_source_means([]) == 3.0
    assert global_prior_mean_of_source_means([4.0, 2.0]) == 3.0


def test_recent_weight_half_life():
    assert abs(recent_weight(30.0) - 0.5) < 1e-9


def test_score_sources_ordering(tmp_path: Path):
    conn = init_db(tmp_path / "db.sqlite")
    now = datetime(2024, 7, 1, tzinfo=timezone.utc)
    # seed sources + links + ratings
    for key, name in (("from:a@ex.com", "A"), ("from:b@ex.com", "B")):
        conn.execute(
            """INSERT INTO sources (source_key, identity_version, lifecycle,
               display_name_observed, created_at, updated_at)
               VALUES (?, 1, 'active', ?, ?, ?)""",
            (key, name, format_utc(now), format_utc(now)),
        )
    conn.execute(
        """INSERT INTO message_source_links VALUES ('mid:1@x', 'from:a@ex.com', ?)""",
        (format_utc(now),),
    )
    conn.execute(
        """INSERT INTO message_source_links VALUES ('mid:2@x', 'from:b@ex.com', ?)""",
        (format_utc(now),),
    )
    # minimal entries so population counts
    run_id = "550e8400-e29b-41d4-a716-446655440000"
    conn.execute(
        """INSERT INTO rollup_runs (
            run_id, started_at, status, entry_index_version, stats_completeness,
            index_source, indexed_at
           ) VALUES (?, ?, 'success', 1, 'full', 'pipeline', ?)""",
        (run_id, format_utc(now), format_utc(now)),
    )
    for i, mk in enumerate(("mid:1@x", "mid:2@x")):
        conn.execute(
            """INSERT INTO rollup_entries (
                run_id, message_key, source_key_observed, section_position,
                entry_position, display_position, links_json
               ) VALUES (?, ?, ?, 0, 0, ?, '[]')""",
            (run_id, mk, "from:a@ex.com" if i == 0 else "from:b@ex.com", i),
        )
    conn.commit()
    set_rating_with_reasons(conn, "mid:1@x", 5, [], now=now)
    set_rating_with_reasons(conn, "mid:2@x", 1, ["not_relevant"], now=now)
    rows = score_sources(conn, now=now)
    assert rows[0].canonical_source_key == "from:a@ex.com"
    assert rows[0].adjusted_score is not None
    # prior includes both source means
    means = [5.0, 1.0]
    prior = sum(means) / 2
    expected = bayesian_adjusted(1, 5.0, prior, PRIOR_WEIGHT)
    assert abs(rows[0].adjusted_score - expected) < 1e-9
    conn.close()


def test_exact_90_day_boundary(tmp_path: Path):
    from rollup.source_quality import _in_recent_window, RECENT_DAYS, SECONDS_PER_DAY

    assert _in_recent_window(RECENT_DAYS * SECONDS_PER_DAY)
    assert not _in_recent_window(RECENT_DAYS * SECONDS_PER_DAY + 1)
    assert not _in_recent_window(-1)
