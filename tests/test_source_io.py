"""Offline tests for source override/alias export and import."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.models import ParsedMessage
from rollup.source_io import export_sources, import_sources
from rollup.source_registry import (
    SourceRegistryError,
    alias_sources,
    load_alias_map,
    load_overrides,
    observe_sources,
    set_overrides,
)
from rollup.state import init_db


def _msg(sender: str = "A <a@b.co>", key: str = "from:a@b.co") -> ParsedMessage:
    return ParsedMessage(
        message_key="mid:1",
        content_hash="h",
        folder_name="tech",
        relative_folder_path="tech",
        subject="S",
        sender=sender,
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
        source_key=key,
    )


def test_export_import_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    observe_sources(conn, [_msg()], generated_at=datetime.now().astimezone())
    set_overrides(
        conn,
        "from:a@b.co",
        updates={
            "priority": 40,
            "newsletter_type": "essay",
            "display_name": "Alpha",
            "enabled": True,
        },
    )
    alias_sources(conn, "from:old@b.co", "from:a@b.co", note="rename")
    out = tmp_path / "sources.json"
    export_sources(conn, out)
    conn.close()

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert any(r["source_key"] == "from:a@b.co" for r in payload["overrides"])
    assert any(r["alias_key"] == "from:old@b.co" for r in payload["aliases"])

    db2 = tmp_path / "other.db"
    conn2 = init_db(db2)
    result = import_sources(conn2, out)
    assert result.created >= 1
    ov = load_overrides(conn2, "from:a@b.co")
    assert ov.priority == 40
    assert ov.newsletter_type == "essay"
    assert ov.display_name == "Alpha"
    aliases = load_alias_map(conn2)
    assert aliases["from:old@b.co"] == "from:a@b.co"
    conn2.close()


def test_import_dry_run_no_writes(tmp_path: Path) -> None:
    db = tmp_path / "rollup.db"
    conn = init_db(db)
    observe_sources(conn, [_msg()], generated_at=datetime.now().astimezone())
    set_overrides(conn, "from:a@b.co", updates={"priority": 10})
    out = tmp_path / "sources.json"
    export_sources(conn, out)
    conn.close()
    before = db.read_bytes()

    conn = init_db(db)
    result = import_sources(conn, out, dry_run=True)
    conn.close()
    assert result.updated >= 1
    assert db.read_bytes() == before


def test_import_rejects_bad_schema_and_enums(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(SourceRegistryError, match="schema_version"):
        import_sources(conn, bad)

    bad.write_text("not-json", encoding="utf-8")
    with pytest.raises(SourceRegistryError, match="Invalid JSON"):
        import_sources(conn, bad)

    bad.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "anchors": [],
                "overrides": [
                    {"source_key": "from:a@b.co", "newsletter_type": "nope"}
                ],
                "aliases": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SourceRegistryError, match="newsletter_type"):
        import_sources(conn, bad)

    bad.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "anchors": [],
                "overrides": [{"source_key": "from:a@b.co", "priority": 999}],
                "aliases": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SourceRegistryError, match="priority"):
        import_sources(conn, bad)
    conn.close()


def test_merge_absent_null_and_replace(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    observe_sources(conn, [_msg()], generated_at=datetime.now().astimezone())
    set_overrides(
        conn,
        "from:a@b.co",
        updates={
            "priority": 20,
            "display_name": "KeepMe",
            "newsletter_type": "essay",
        },
    )
    merge_path = tmp_path / "merge.json"
    merge_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "anchors": [{"source_key": "from:a@b.co"}],
                "overrides": [
                    {
                        "source_key": "from:a@b.co",
                        "priority": 55,
                        "newsletter_type": None,
                        # display_name absent → preserved under merge
                    }
                ],
                "aliases": [],
            }
        ),
        encoding="utf-8",
    )
    import_sources(conn, merge_path, merge=True)
    ov = load_overrides(conn, "from:a@b.co")
    assert ov.priority == 55
    assert ov.newsletter_type is None
    assert ov.display_name == "KeepMe"

    replace_path = tmp_path / "replace.json"
    replace_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "anchors": [{"source_key": "from:a@b.co"}],
                "overrides": [
                    {
                        "source_key": "from:a@b.co",
                        "priority": 1,
                        "display_name": "Replaced",
                    }
                ],
                "aliases": [],
            }
        ),
        encoding="utf-8",
    )
    import_sources(conn, replace_path, merge=False)
    ov2 = load_overrides(conn, "from:a@b.co")
    assert ov2.priority == 1
    assert ov2.display_name == "Replaced"
    assert ov2.newsletter_type is None
    conn.close()
