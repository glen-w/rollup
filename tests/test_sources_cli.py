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
