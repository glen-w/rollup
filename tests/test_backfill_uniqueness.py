"""Backfill uniqueness and caller-owned transaction behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest

from rollup.reader_body_backfill import (
    BackfillError,
    BackfillScope,
    scan_backfill_candidates,
    validate_newsletter_root,
)
from rollup.state import init_db


def test_validate_newsletter_root_containment(tmp_path: Path):
    mail = tmp_path / "mail"
    news = mail / "Newsletters.sbd"
    other = tmp_path / "other"
    news.mkdir(parents=True)
    other.mkdir()
    validate_newsletter_root(newsletter_root=news, mail_root=mail)
    with pytest.raises(BackfillError):
        validate_newsletter_root(newsletter_root=other, mail_root=mail)


def test_ambiguous_duplicate_excluded(tmp_path: Path, monkeypatch):
    """Differing hashes for the same message_key must exclude the key."""
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    conn.execute(
        """INSERT INTO rollup_runs (
             run_id, started_at, status, entry_index_version, stats_completeness,
             index_warning_count, degraded, index_source, indexed_at
           ) VALUES ('r1', '2024-01-01T00:00:00Z', 'success', 1, 'full', 0, 0, 'pipeline', '2024-01-01T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO rollup_entries (
             run_id, message_key, section_key, section_position, entry_position,
             display_position, links_json
           ) VALUES ('r1', 'mid:dup@x', 's', 0, 0, 0, '[]')"""
    )
    conn.commit()

    class FakeParsed:
        def __init__(self, key, h, body):
            self.message_key = key
            self.content_hash = h
            self.body_text = body

    class FakeFolder:
        mbox_path = tmp_path / "f"
        folder_name = "f"
        relative_path = "f"

    FakeFolder.mbox_path.write_text("", encoding="utf-8")

    messages = [
        (FakeParsed("mid:dup@x", "a" * 64, "body-one"), None),
        (FakeParsed("mid:dup@x", "b" * 64, "body-two"), None),
    ]

    def fake_iter_mbox(_root):
        yield FakeFolder

    def fake_iter_parsed(*_a, **_k):
        yield from messages

    monkeypatch.setattr(
        "rollup.reader_body_backfill.iter_mbox_files", fake_iter_mbox
    )
    monkeypatch.setattr(
        "rollup.reader_body_backfill.iter_parsed_messages", fake_iter_parsed
    )
    monkeypatch.setattr(
        "rollup.reader_body_backfill.snapshot_mbox", lambda _p: None
    )

    plan = scan_backfill_candidates(
        conn,
        newsletter_root=tmp_path,
        scope=BackfillScope(run_id="r1"),
    )
    assert "mid:dup@x" in plan.ambiguous_keys
    assert plan.writes == ()
    conn.close()


def test_identical_hash_deduped(tmp_path: Path, monkeypatch):
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    conn.execute(
        """INSERT INTO rollup_runs (
             run_id, started_at, status, entry_index_version, stats_completeness,
             index_warning_count, degraded, index_source, indexed_at
           ) VALUES ('r1', '2024-01-01T00:00:00Z', 'success', 1, 'full', 0, 0, 'pipeline', '2024-01-01T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO rollup_entries (
             run_id, message_key, section_key, section_position, entry_position,
             display_position, links_json
           ) VALUES ('r1', 'mid:same@x', 's', 0, 0, 0, '[]')"""
    )
    conn.commit()

    class FakeParsed:
        def __init__(self, key, h, body):
            self.message_key = key
            self.content_hash = h
            self.body_text = body

    class FakeFolder:
        mbox_path = tmp_path / "f"
        folder_name = "f"
        relative_path = "f"

    FakeFolder.mbox_path.write_text("", encoding="utf-8")
    h = "c" * 64
    messages = [
        (FakeParsed("mid:same@x", h, "same-body"), None),
        (FakeParsed("mid:same@x", h, "same-body"), None),
    ]

    monkeypatch.setattr(
        "rollup.reader_body_backfill.iter_mbox_files",
        lambda _r: [FakeFolder],
    )
    monkeypatch.setattr(
        "rollup.reader_body_backfill.iter_parsed_messages",
        lambda *_a, **_k: messages,
    )
    monkeypatch.setattr(
        "rollup.reader_body_backfill.snapshot_mbox", lambda _p: None
    )

    plan = scan_backfill_candidates(
        conn,
        newsletter_root=tmp_path,
        scope=BackfillScope(run_id="r1"),
    )
    assert plan.ambiguous_keys == frozenset()
    assert len(plan.writes) == 1
    conn.close()
