"""Offline tests for reader body prune / delete / backfill."""

from __future__ import annotations

import mailbox
from email.message import EmailMessage
from pathlib import Path

from rollup.parse import compute_content_hash, iter_parsed_messages
from rollup.reader_bodies import make_reader_body_write
from rollup.reader_body_backfill import (
    BackfillScope,
    delete_all_bodies,
    prune_orphans,
    run_backfill,
)
from rollup.reader_body_store import get_reader_body, upsert_reader_bodies_v2
from rollup.state import init_db
from rollup.utc import format_utc, now_utc


def _seed_entry(
    conn, message_key: str, run_id: str = "550e8400-e29b-41d4-a716-446655440000"
) -> None:
    now = format_utc(now_utc())
    conn.execute(
        """INSERT OR IGNORE INTO rollup_runs (
            run_id, started_at, status, entry_index_version, stats_completeness,
            index_source, indexed_at
           ) VALUES (?, ?, 'success', 1, 'full', 'pipeline', ?)""",
        (run_id, now, now),
    )
    conn.execute(
        """INSERT OR IGNORE INTO rollup_entries (
            run_id, message_key, source_key_observed, section_position,
            entry_position, display_position, links_json
           ) VALUES (?, ?, 'from:a@ex.com', 0, 0, 0, '[]')""",
        (run_id, message_key),
    )


def _write_tiny_mbox(sbd: Path) -> Path:
    sbd.mkdir(parents=True, exist_ok=True)
    mbox_file = sbd / "tech"
    msg = EmailMessage()
    msg["Message-ID"] = "<backfill-test@example.com>"
    msg["From"] = "Sender <s@example.com>"
    msg["Subject"] = "Backfill me"
    msg["Date"] = "Thu, 01 Jan 2026 12:00:00 +0000"
    msg.set_content("Body for backfill test.")
    box = mailbox.mbox(str(mbox_file))
    box.add(msg)
    box.close()
    return mbox_file


def test_prune_orphans_dry_and_real(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    kept = "mid:kept@ex.com"
    _seed_entry(conn, kept)
    upsert_reader_bodies_v2(
        conn,
        [
            make_reader_body_write(kept, compute_content_hash("k"), "kept"),
            make_reader_body_write(
                "mid:orphan@ex.com", compute_content_hash("o"), "orphan"
            ),
        ],
        seen_at=format_utc(now_utc()),
    )
    conn.commit()
    assert prune_orphans(conn, dry_run=True) == 1
    assert get_reader_body(conn, "mid:orphan@ex.com") is not None
    assert prune_orphans(conn, dry_run=False) == 1
    assert get_reader_body(conn, "mid:orphan@ex.com") is None
    assert get_reader_body(conn, kept) is not None
    conn.close()


def test_delete_all_bodies(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    key = "mid:del@ex.com"
    _seed_entry(conn, key)
    upsert_reader_bodies_v2(
        conn,
        [make_reader_body_write(key, compute_content_hash("z"), "z")],
        seen_at=format_utc(now_utc()),
    )
    conn.commit()
    assert delete_all_bodies(conn, dry_run=True) == 1
    assert get_reader_body(conn, key) is not None
    assert delete_all_bodies(conn, dry_run=False) == 1
    assert get_reader_body(conn, key) is None
    conn.close()


def test_backfill_missing_source(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    key = "mid:missing@ex.com"
    _seed_entry(conn, key)
    conn.commit()
    mail_root = tmp_path / "empty.sbd"
    mail_root.mkdir()
    result = run_backfill(
        conn,
        mail_root=mail_root,
        scope=BackfillScope(),
        dry_run=True,
    )
    assert result.candidates == 1
    assert result.matched == 0
    assert result.source_missing == 1
    conn.close()


def test_backfill_from_tiny_mbox(tmp_path: Path) -> None:
    sbd = tmp_path / "Newsletters.sbd"
    mbox_file = _write_tiny_mbox(sbd)
    parsed_key = None
    for parsed, err in iter_parsed_messages(
        mbox_file, "tech", "tech", max_body_chars=200_000, max_display_links=8
    ):
        if parsed and not err:
            parsed_key = parsed.message_key
            break
    assert parsed_key

    conn = init_db(tmp_path / "rollup.db")
    _seed_entry(conn, parsed_key)
    conn.commit()

    dry = run_backfill(conn, mail_root=sbd, scope=BackfillScope(), dry_run=True)
    assert dry.candidates == 1
    assert dry.matched == 1

    real = run_backfill(conn, mail_root=sbd, scope=BackfillScope(), dry_run=False)
    assert real.inserted == 1
    rec = get_reader_body(conn, parsed_key)
    assert rec is not None
    assert "backfill" in rec.body_text.lower()
    conn.close()
