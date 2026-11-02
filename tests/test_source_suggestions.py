"""Live source-intelligence suggestions (no mail-root writes)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from rollup.cli import build_parser
from rollup.models import ParsedMessage
from rollup.parse import compute_content_hash
from rollup.source_registry import get_source_record, observe_sources, set_overrides
from rollup.source_suggestions import collect_suggestions
from rollup.sources_cmd import cmd_sources
from rollup.state import init_db


NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)


def _msg(source: str, key: str = "mid:1@x", subject: str = "S") -> ParsedMessage:
    body = subject
    return ParsedMessage(
        message_key=key,
        content_hash=compute_content_hash(body),
        folder_name="tech",
        relative_folder_path="tech",
        subject=subject,
        sender=f"A <{source.split(':', 1)[-1]}>",
        date_raw="",
        date_parsed=NOW,
        body_text=body,
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=1,
        preview=body,
        parse_warnings=(),
        source_key=source,
    )


def _seed_run(conn, run_id: str, rows: list[tuple[str, str, str, str | None]]) -> None:
    now = "2026-11-02T00:00:00Z"
    conn.execute(
        """INSERT INTO rollup_runs (
            run_id, started_at, status, stats_completeness, index_source, indexed_at
           ) VALUES (?, ?, 'success', 'full', 'pipeline', ?)""",
        (run_id, now, now),
    )
    for i, (mk, source, subject, link) in enumerate(rows):
        conn.execute(
            """INSERT INTO rollup_entries (
                run_id, message_key, source_key_observed, section_position,
                entry_position, display_position, subject, primary_link, links_json
               ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, '[]')""",
            (run_id, mk, source, i, i, subject, link),
        )
        conn.execute(
            """INSERT OR REPLACE INTO message_source_links (
                message_key, source_key_observed, updated_at
               ) VALUES (?, ?, ?)""",
            (mk, source, now),
        )


def test_empty_registry_no_suggestions(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    assert collect_suggestions(conn, now=NOW) == ()
    conn.close()


def test_unused_8w(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    observe_sources(
        conn,
        [_msg("from:old@ex.com", "mid:old@x")],
        generated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    items = collect_suggestions(conn, now=NOW)
    assert any(s.code == "unused_8w" and s.source_key == "from:old@ex.com" for s in items)
    conn.close()


def test_disabled_source_has_no_suggestions(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    observe_sources(
        conn,
        [_msg("from:old@ex.com", "mid:old@x")],
        generated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    set_overrides(conn, "from:old@ex.com", updates={"enabled": False})
    items = collect_suggestions(conn, now=NOW)
    assert items == ()
    conn.close()


def test_mostly_dismissed(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    src = "from:noise@ex.com"
    msgs = [_msg(src, f"mid:d{i}@x") for i in range(5)]
    observe_sources(conn, msgs, generated_at=NOW)
    rows = [(m.message_key, src, m.subject, None) for m in msgs]
    _seed_run(conn, "run-d", rows)
    now = "2026-11-02T00:00:00Z"
    for m in msgs:
        conn.execute(
            """INSERT INTO message_interaction (
                message_key, dismissed_at, updated_at
               ) VALUES (?, ?, ?)""",
            (m.message_key, now, now),
        )
    conn.commit()
    items = collect_suggestions(conn, now=NOW)
    assert any(
        s.code == "mostly_dismissed" and s.source_key == src for s in items
    )
    conn.close()


def test_consider_lower_priority(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    src = "from:low@ex.com"
    msgs = [_msg(src, f"mid:l{i}@x") for i in range(5)]
    observe_sources(conn, msgs, generated_at=NOW)
    set_overrides(conn, src, updates={"priority": 80})
    _seed_run(conn, "run-l", [(m.message_key, src, m.subject, None) for m in msgs])
    now = "2026-11-02T00:00:00Z"
    for m in msgs:
        conn.execute(
            """INSERT INTO message_ratings (message_key, stars, created_at, updated_at)
               VALUES (?, 1, ?, ?)""",
            (m.message_key, now, now),
        )
    conn.commit()
    items = collect_suggestions(conn, now=NOW)
    hit = next(s for s in items if s.code == "consider_lower_priority")
    assert hit.source_key == src
    assert hit.apply == "lower_priority"
    conn.close()


def test_overlapping_coverage(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    a, b = "from:a@ex.com", "from:b@ex.com"
    observe_sources(
        conn,
        [_msg(a, "mid:a0@x"), _msg(b, "mid:b0@x")],
        generated_at=NOW,
    )
    url = "https://example.com/shared-story"
    rows = []
    for i in range(3):
        rows.append((f"mid:a{i}@x", a, f"Shared story {i} extra words here", url))
        rows.append((f"mid:b{i}@x", b, f"Shared story {i} extra words here", url))
    _seed_run(conn, "run-o", rows)
    conn.commit()
    items = collect_suggestions(conn, now=NOW)
    keys = {s.source_key for s in items if s.code == "overlapping_coverage"}
    assert keys == {a, b}
    conn.close()


def test_mostly_duplicate(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    weak, strong = "from:weak@ex.com", "from:strong@ex.com"
    observe_sources(
        conn,
        [_msg(weak, "mid:w0@x"), _msg(strong, "mid:s0@x")],
        generated_at=NOW,
    )
    rows = []
    now = "2026-11-02T00:00:00Z"
    for i in range(4):
        title = f"Unique briefing topic number {i} details"
        rows.append((f"mid:w{i}@x", weak, title, f"https://ex.com/t{i}"))
        rows.append((f"mid:s{i}@x", strong, title, f"https://ex.com/t{i}"))
        conn.execute(
            """INSERT INTO message_ratings (message_key, stars, created_at, updated_at)
               VALUES (?, 5, ?, ?)""",
            (f"mid:s{i}@x", now, now),
        )
    _seed_run(conn, "run-dup", rows)
    conn.commit()
    items = collect_suggestions(conn, now=NOW)
    dup = [s for s in items if s.code == "mostly_duplicate"]
    assert any(s.source_key == weak and s.related_source_key == strong for s in dup)
    conn.close()


def test_apply_only_via_set_overrides(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    observe_sources(
        conn,
        [_msg("from:old@ex.com", "mid:old@x")],
        generated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    rec = get_source_record(conn, "from:old@ex.com")
    assert rec.policy.enabled is True
    set_overrides(conn, "from:old@ex.com", updates={"enabled": False})
    rec = get_source_record(conn, "from:old@ex.com")
    assert rec.policy.enabled is False
    assert collect_suggestions(conn, now=NOW) == ()
    conn.close()


def test_sources_suggest_cli_json(tmp_path: Path, capsys) -> None:
    state = tmp_path / "state"
    state.mkdir()
    conn = init_db(state / "rollup.db")
    observe_sources(
        conn,
        [_msg("from:old@ex.com", "mid:old@x")],
        generated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    conn.close()
    args = build_parser().parse_args(
        ["sources", "suggest", "--state-dir", str(state), "--json"]
    )
    assert cmd_sources(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["suggestions"][0]["code"] == "unused_8w"
