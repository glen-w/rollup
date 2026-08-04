"""CLI tests for rollup sources."""

from __future__ import annotations

import json
from pathlib import Path

from rollup.cli import build_parser
from rollup.sources_cmd import cmd_sources
from rollup.state import init_db
from rollup.source_registry import observe_sources, set_overrides
from rollup.models import ParsedMessage
from datetime import datetime, timezone


def _msg() -> ParsedMessage:
    return ParsedMessage(
        message_key="mid:1",
        content_hash="h",
        folder_name="tech",
        relative_folder_path="tech",
        subject="S",
        sender="A <a@b.co>",
        date_raw="",
        date_parsed=datetime(2026, 1, 1, tzinfo=timezone.utc),
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
        source_key="from:a@b.co",
    )


def test_sources_list_json(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    conn = init_db(state / "rollup.db")
    observe_sources(conn, [_msg()], generated_at=datetime.now().astimezone())
    conn.close()
    args = build_parser().parse_args(
        ["sources", "list", "--state-dir", str(state), "--json"]
    )
    # Capture via cmd
    import io
    import sys

    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        assert cmd_sources(args) == 0
    finally:
        sys.stdout = old
    data = json.loads(buf.getvalue())
    assert data["schema_version"] == 1
    assert data["sources"][0]["source_key"] == "from:a@b.co"


def test_sources_set_idempotent(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    conn = init_db(state / "rollup.db")
    observe_sources(conn, [_msg()], generated_at=datetime.now().astimezone())
    conn.close()
    for _ in range(2):
        args = build_parser().parse_args(
            [
                "sources",
                "set",
                "from:a@b.co",
                "--priority",
                "50",
                "--state-dir",
                str(state),
            ]
        )
        assert cmd_sources(args) == 0
    conn = init_db(state / "rollup.db")
    from rollup.source_registry import get_source_record

    rec = get_source_record(conn, "from:a@b.co")
    assert rec.overrides.priority == 50
    conn.close()


def test_sources_dry_run_no_write(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    db = state / "rollup.db"
    conn = init_db(db)
    observe_sources(conn, [_msg()], generated_at=datetime.now().astimezone())
    conn.close()
    before = db.read_bytes()
    args = build_parser().parse_args(
        [
            "sources",
            "set",
            "from:a@b.co",
            "--disabled",
            "--dry-run",
            "--state-dir",
            str(state),
        ]
    )
    assert cmd_sources(args) == 0
    assert db.read_bytes() == before


def test_sources_export_import_and_doctor(tmp_path: Path) -> None:
    import io
    import sys

    state = tmp_path / "state"
    state.mkdir()
    conn = init_db(state / "rollup.db")
    observe_sources(conn, [_msg()], generated_at=datetime.now().astimezone())
    set_overrides(conn, "from:a@b.co", updates={"priority": 33})
    conn.close()

    out = tmp_path / "export.json"
    args = build_parser().parse_args(
        [
            "sources",
            "export",
            "--out",
            str(out),
            "--state-dir",
            str(state),
            "--mail-root",
            str(tmp_path / "mail"),
        ]
    )
    assert cmd_sources(args) == 0
    assert out.is_file()

    state2 = tmp_path / "state2"
    state2.mkdir()
    init_db(state2 / "rollup.db").close()
    args = build_parser().parse_args(
        [
            "sources",
            "import",
            "--from",
            str(out),
            "--state-dir",
            str(state2),
            "--mail-root",
            str(tmp_path / "mail"),
        ]
    )
    assert cmd_sources(args) == 0

    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        args = build_parser().parse_args(
            ["sources", "doctor", "--state-dir", str(state2), "--json"]
        )
        assert cmd_sources(args) == 0
    finally:
        sys.stdout = old
    report = json.loads(buf.getvalue())
    assert report["ok"] is True
    ids = {c["id"] for c in report["checks"]}
    assert "source_registry_schema" in ids


def test_import_replace_all_requires_ack(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    init_db(state / "rollup.db").close()
    src = tmp_path / "empty.json"
    src.write_text(
        json.dumps(
            {"schema_version": 1, "anchors": [], "overrides": [], "aliases": []}
        ),
        encoding="utf-8",
    )
    args = build_parser().parse_args(
        [
            "sources",
            "import",
            "--from",
            str(src),
            "--replace-all",
            "--state-dir",
            str(state),
            "--mail-root",
            str(tmp_path / "mail"),
        ]
    )
    assert cmd_sources(args) == 1


def test_source_doctor_orphan_alias_fails(tmp_path: Path) -> None:
    from rollup.source_doctor import run_source_doctor

    db = tmp_path / "rollup.db"
    conn = init_db(db)
    now = datetime.now().astimezone().isoformat()
    conn.execute(
        """INSERT INTO sources (source_key, identity_version, lifecycle,
           display_name_observed, created_at, updated_at)
           VALUES ('from:missing@ex.com', 1, 'active', 'M', ?, ?)""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO source_aliases (alias_key, canonical_source_key, created_at, note)
           VALUES ('from:alias@ex.com', 'from:missing@ex.com', ?, NULL)""",
        (now,),
    )
    conn.commit()
    conn.close()

    # Drop the canonical row with FK enforcement off so the alias becomes orphaned.
    conn = init_db(db)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM sources WHERE source_key = 'from:missing@ex.com'")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    report = run_source_doctor(conn)
    assert report["ok"] is False
    orphan = next(c for c in report["checks"] if c["id"] == "source_orphan_aliases")
    assert orphan["status"] == "fail"
    conn.close()
