"""State/registry tests for source schema and observation idempotency."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rollup.models import ParsedMessage
from rollup.source_registry import (
    alias_sources,
    get_source_record,
    load_SourceRegistrySnapshot,
    observe_sources,
    set_overrides,
)
from rollup.state import SCHEMA_VERSION, get_schema_version, init_db, init_db_with_summaries


def _msg(
    key: str,
    *,
    source_key: str = "from:a@b.co",
    sender: str = "A <a@b.co>",
    dated: bool = True,
) -> ParsedMessage:
    return ParsedMessage(
        message_key=key,
        content_hash="h",
        folder_name="tech",
        relative_folder_path="tech",
        subject="Subj",
        sender=sender,
        date_raw="Tue, 01 Jul 2026 12:00:00 +0000",
        date_parsed=datetime(2026, 1, 1, 12, tzinfo=timezone.utc) if dated else None,
        body_text="body",
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=1,
        preview="body",
        parse_warnings=(),
        source_key=source_key,
        list_id=None,
    )


def test_schema_migrates_to_v7(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    assert get_schema_version(conn) == 7 == SCHEMA_VERSION
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "sources" in tables
    assert "source_cadence_samples" in tables
    assert "source_observation_dedup" in tables
    conn.close()


def test_observe_idempotent(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    now = datetime.now().astimezone()
    msgs = [_msg("mid:1"), _msg("mid:2")]
    r1 = observe_sources(conn, msgs, generated_at=now)
    assert r1.discovered_this_run == 1
    rec = get_source_record(conn, "from:a@b.co")
    assert rec.observation.message_count_total == 2
    r2 = observe_sources(conn, msgs, generated_at=now)
    assert r2.discovered_this_run == 0
    rec2 = get_source_record(conn, "from:a@b.co")
    assert rec2.observation.message_count_total == 2
    conn.close()


def test_overrides_survive_observation(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    now = datetime.now().astimezone()
    observe_sources(conn, [_msg("mid:1")], generated_at=now)
    set_overrides(conn, "from:a@b.co", updates={"enabled": False, "priority": 80})
    observe_sources(conn, [_msg("mid:2")], generated_at=now)
    rec = get_source_record(conn, "from:a@b.co")
    assert rec.overrides.enabled is False
    assert rec.overrides.priority == 80
    assert rec.observation.message_count_total == 2
    conn.close()


def test_alias_merge_no_recount(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    now = datetime.now().astimezone()
    observe_sources(
        conn,
        [_msg("mid:1", source_key="from:old@b.co", sender="Old <old@b.co>")],
        generated_at=now,
    )
    observe_sources(
        conn,
        [_msg("mid:2", source_key="from:new@b.co", sender="New <new@b.co>")],
        generated_at=now,
    )
    alias_sources(conn, "from:old@b.co", "from:new@b.co")
    rec = get_source_record(conn, "from:new@b.co")
    assert rec.observation.message_count_total == 2
    old = conn.execute(
        "SELECT lifecycle, superseded_by FROM sources WHERE source_key = ?",
        ("from:old@b.co",),
    ).fetchone()
    assert old[0] == "superseded"
    assert old[1] == "from:new@b.co"
    conn.close()


def test_snapshot_bounded(tmp_path: Path) -> None:
    conn = init_db_with_summaries(tmp_path / "rollup.db")
    now = datetime.now().astimezone()
    observe_sources(conn, [_msg("mid:1")], generated_at=now)
    observe_sources(
        conn,
        [_msg("mid:9", source_key="from:other@b.co", sender="O <other@b.co>")],
        generated_at=now,
    )
    snap = load_SourceRegistrySnapshot(conn, {"from:a@b.co"})
    assert "from:a@b.co" in snap.policies
    assert "from:other@b.co" not in snap.policies
    assert snap.known_count == 2
    conn.close()
