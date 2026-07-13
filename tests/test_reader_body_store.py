"""Tests for reader body SQLite upsert / text-version upgrades."""

from __future__ import annotations

from pathlib import Path

from rollup.parse import compute_content_hash
from rollup.reader_bodies import READER_TEXT_VERSION, make_reader_body_write
from rollup.reader_body_store import get_reader_body, upsert_reader_bodies_v2
from rollup.state import init_db
from rollup.utc import format_utc, now_utc


def test_upsert_v2_prepares_layout_chrome(tmp_path: Path) -> None:
    db = init_db(tmp_path / "rollup.db")
    key = "mid:chrome@example.com"
    dirty = (
        "Jul 7| | | •| | Paid\n"
        "---|---|---|---|---|---\n"
        "| | [READ IN APP](https://example.com/app)\n"
        "Body copy.\n"
    )
    w = make_reader_body_write(key, compute_content_hash(dirty), dirty)
    stats = upsert_reader_bodies_v2(db, [w], seen_at=format_utc(now_utc()))
    db.commit()
    assert stats.inserted == 1
    rec = get_reader_body(db, key)
    assert rec is not None
    assert rec.reader_text_version == READER_TEXT_VERSION
    assert "|" not in rec.body_text
    assert "---" not in rec.body_text
    assert "[READ IN APP](https://example.com/app)" in rec.body_text
    assert "Jul 7" in rec.body_text and "Paid" in rec.body_text
    db.close()


def test_upsert_v2_upgrades_when_text_version_advances(tmp_path: Path) -> None:
    db = init_db(tmp_path / "rollup.db")
    key = "mid:upgrade@example.com"
    dirty = "A| | B\n---|---\nKept prose.\n"
    ch = compute_content_hash(dirty)
    w = make_reader_body_write(key, ch, dirty)
    upsert_reader_bodies_v2(db, [w], seen_at=format_utc(now_utc()))
    db.commit()
    # Force an older prepared snapshot with chrome still present.
    db.execute(
        """UPDATE message_reader_bodies
           SET body_text = ?, reader_text_version = 1, stored_body_hash = ?
           WHERE message_key = ?""",
        (dirty, "a" * 64, key),
    )
    db.commit()
    stats = upsert_reader_bodies_v2(db, [w], seen_at=format_utc(now_utc()))
    db.commit()
    assert stats.updated == 1
    assert stats.conflicts == 0
    rec = get_reader_body(db, key)
    assert rec is not None
    assert rec.reader_text_version == READER_TEXT_VERSION
    assert "|" not in rec.body_text
    assert "---" not in rec.body_text
    assert "Kept prose." in rec.body_text
    db.close()
