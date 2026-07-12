"""Rating and interaction service tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.interaction import dismiss, get_interaction, mark_read, mark_unread, save
from rollup.ratings import RatingError, get_rating, set_rating_with_reasons
from rollup.state import init_db


@pytest.fixture
def conn(tmp_path: Path):
    c = init_db(tmp_path / "rollup.db")
    yield c
    c.close()


def test_rating_replace_all_reasons(conn):
    now = datetime(2024, 6, 1, tzinfo=timezone.utc)
    set_rating_with_reasons(
        conn, "mid:a@x", 2, ["not_relevant", "too_long"], now=now
    )
    r = get_rating(conn, "mid:a@x")
    assert r is not None
    assert r.stars == 2
    assert set(r.reason_codes) == {"not_relevant", "too_long"}
    set_rating_with_reasons(conn, "mid:a@x", 5, ["concise"], now=now)
    r2 = get_rating(conn, "mid:a@x")
    assert r2.stars == 5
    assert r2.reason_codes == ("concise",)
    assert r2.created_at == r.created_at


def test_rating_empty_reasons_clears(conn):
    now = datetime(2024, 6, 1, tzinfo=timezone.utc)
    set_rating_with_reasons(conn, "mid:a@x", 4, ["great_links"], now=now)
    set_rating_with_reasons(conn, "mid:a@x", 4, [], now=now)
    assert get_rating(conn, "mid:a@x").reason_codes == ()


def test_polarity_enforced(conn):
    now = datetime(2024, 6, 1, tzinfo=timezone.utc)
    with pytest.raises(RatingError):
        set_rating_with_reasons(conn, "mid:a@x", 1, ["concise"], now=now)
    with pytest.raises(RatingError):
        set_rating_with_reasons(conn, "mid:a@x", 5, ["too_long"], now=now)


def test_interaction_orthogonal(conn):
    now = datetime(2024, 6, 1, tzinfo=timezone.utc)
    key = "mid:a@x"
    mark_read(conn, key, now=now)
    save(conn, key, now=now)
    dismiss(conn, key, now=now)
    st = get_interaction(conn, key)
    assert st.is_read and st.is_saved and st.is_dismissed
    mark_unread(conn, key, now=now)
    st2 = get_interaction(conn, key)
    assert not st2.is_read and st2.is_saved and st2.is_dismissed
