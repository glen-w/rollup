"""Schema v8 migration and indexing contract tests."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.links_sanitize import build_links_json
from rollup.models import (
    ClassifiedMessage,
    DigestEntry,
    DigestReport,
    DigestStats,
    ParsedMessage,
)
from rollup.ratings import get_rating, set_rating_with_reasons
from rollup.run_index import (
    IndexEntry,
    RunIndexError,
    RunIndexPayload,
    backfill_run_from_manifest,
    flatten_report_entries,
    index_rollup_run,
)
from rollup.state import (
    SOURCE_REGISTRY_SCHEMA_V7,
    SCHEMA_VERSION,
    connect_db,
    ensure_source_registry_schema,
    ensure_web_schema,
    get_schema_version,
    init_db,
)
from rollup.utc import format_utc


NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _make_v7_db(path: Path) -> None:
    """Build a schema-v7 DB with old source_overrides CHECK (no web)."""
    conn = connect_db(path)
    conn.executescript(
        """
        CREATE TABLE seen_messages (
            message_key TEXT PRIMARY KEY,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL
        );
        INSERT INTO schema_version (id, version) VALUES (1, 1);
        """
    )
    conn.commit()
    ensure_source_registry_schema(conn)
    # Force old CHECK without 'web' if CREATE IF NOT EXISTS skipped a fresh table
    # Ensure an override row exists to preserve across rebuild
    iso = format_utc(NOW)
    conn.execute(
        """INSERT OR IGNORE INTO sources
           (source_key, identity_version, lifecycle, display_name_observed, created_at, updated_at)
           VALUES ('from:a@ex.com', 1, 'active', 'A', ?, ?)""",
        (iso, iso),
    )
    conn.execute(
        """INSERT OR REPLACE INTO source_overrides
           (source_key, display_name, updated_at, updated_by)
           VALUES ('from:a@ex.com', 'Alpha', ?, 'cli')""",
        (iso,),
    )
    conn.execute("UPDATE schema_version SET version = 7 WHERE id = 1")
    conn.commit()
    # Rebuild overrides table to the pre-web CHECK shape if current already has web
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='source_overrides'"
    ).fetchone()[0]
    if "'web'" in sql:
        before = conn.execute("SELECT COUNT(*) FROM source_overrides").fetchone()[0]
        conn.executescript(
            """
            CREATE TABLE source_overrides_old (
                source_key TEXT PRIMARY KEY,
                enabled INTEGER,
                always_surface INTEGER,
                priority INTEGER,
                newsletter_type TEXT,
                grouping_policy TEXT,
                summary_profile TEXT,
                expected_cadence TEXT,
                display_name TEXT,
                notes TEXT,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL CHECK (updated_by IN ('cli', 'import')),
                FOREIGN KEY (source_key) REFERENCES sources(source_key) ON DELETE RESTRICT
            );
            INSERT INTO source_overrides_old
              SELECT source_key, enabled, always_surface, priority, newsletter_type,
                     grouping_policy, summary_profile, expected_cadence, display_name,
                     notes, updated_at, updated_by FROM source_overrides;
            DROP TABLE source_overrides;
            ALTER TABLE source_overrides_old RENAME TO source_overrides;
            """
        )
        after = conn.execute("SELECT COUNT(*) FROM source_overrides").fetchone()[0]
        assert after == before
        conn.execute("UPDATE schema_version SET version = 7 WHERE id = 1")
        conn.commit()
    conn.close()


def test_v7_to_v8_migration_preserves_overrides(tmp_path: Path):
    db = tmp_path / "rollup.db"
    _make_v7_db(db)
    conn = connect_db(db)
    assert get_schema_version(conn) == 7
    assert "'web'" not in conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='source_overrides'"
    ).fetchone()[0]
    ensure_web_schema(conn)
    assert get_schema_version(conn) == 8
    assert "rollup_runs" in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='source_overrides'"
    ).fetchone()[0]
    assert "web" in sql
    row = conn.execute(
        "SELECT display_name, updated_by FROM source_overrides WHERE source_key=?",
        ("from:a@ex.com",),
    ).fetchone()
    assert row == ("Alpha", "cli")
    conn.close()


def _entry(
    message_key: str,
    *,
    display_position: int = 0,
    links_json: str | None = None,
    source: str = "from:a@example.com",
) -> IndexEntry:
    return IndexEntry(
        message_key=message_key,
        source_key_observed=source,
        group_id=None,
        group_type=None,
        group_display_name=None,
        section_key="tech",
        section_position=0,
        group_position=None,
        entry_position=0,
        display_position=display_position,
        folder_name="tech",
        subject="Subj",
        sender="A",
        date_parsed=format_utc(NOW),
        date_raw="",
        newsletter_type="essay",
        summary="sum",
        summary_source="none",
        primary_link="https://example.com/",
        links_json=links_json or build_links_json([("https://example.com/", "Ex")]),
    )


def _payload(run_id: str, entries: list[IndexEntry], **over) -> RunIndexPayload:
    data = dict(
        run_id=run_id,
        started_at=format_utc(NOW),
        completed_at=format_utc(NOW),
        status="success",
        mode="manual",
        rollup_version="0.5.0",
        manifest_schema_version=2,
        report_schema_version=1,
        stats_completeness="full",
        window_start=format_utc(NOW),
        window_end=format_utc(NOW),
        lookback_days=7,
        digest_fingerprint="fp",
        messages_included=len(entries),
        messages_skipped_outside_window=0,
        messages_skipped_seen_undated=0,
        messages_deduped=0,
        messages_skipped_disabled_source=0,
        groups_created=0,
        sources_included=1,
        summaries_ollama=0,
        summaries_cache=0,
        summaries_fallback=0,
        summaries_errors=0,
        summaries_final_review_applied=0,
        group_summaries_succeeded=0,
        warning_count=0,
        degraded=False,
        manifest_relpath=None,
        markdown_relpath="x.md",
        html_relpath="x.html",
        index_source="pipeline",
        entries=entries,
        expected_entry_count=len(entries),
    )
    data.update(over)
    return RunIndexPayload(**data)


def test_failed_index_leaves_prior_intact(tmp_path: Path):
    db = tmp_path / "rollup.db"
    run_id = str(uuid.uuid4())
    index_rollup_run(db, _payload(run_id, [_entry("mid:one@x")]))
    conn = init_db(db)
    assert conn.execute("SELECT COUNT(*) FROM rollup_entries").fetchone()[0] == 1
    conn.close()

    bad = _entry("mid:two@x", display_position=0)
    bad.links_json = '{"v":1,"items":[{"href":"javascript:alert(1)","label":"x"}]}'
    with pytest.raises(RunIndexError):
        # Force validation failure path inside index_rollup_run
        index_rollup_run(db, _payload(run_id, [bad]))

    conn = init_db(db)
    keys = [
        r[0]
        for r in conn.execute(
            "SELECT message_key FROM rollup_entries WHERE run_id=?", (run_id,)
        )
    ]
    assert keys == ["mid:one@x"]
    conn.close()


def test_duplicate_message_key_collision_warning(tmp_path: Path):
    from rollup.run_index import flatten_report_entries

    def entry(key: str) -> DigestEntry:
        parsed = ParsedMessage(
            message_key=key,
            content_hash="c" * 64,
            folder_name="tech",
            relative_folder_path="tech",
            subject="Hello",
            sender="A <a@example.com>",
            date_raw="",
            date_parsed=NOW,
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
            source_key="from:a@example.com",
            list_id=None,
        )
        return DigestEntry(
            classified=ClassifiedMessage(
                parsed=parsed, newsletter_type="essay", classification_scores=()
            ),
            summary="s",
            summary_source="none",
        )

    report = DigestReport(
        generated_at=NOW,
        lookback_days=7,
        window_start=NOW,
        window_end=NOW,
        dated_by_folder={"tech": (entry("mid:dup@x"), entry("mid:dup@x"))},
        undated=(),
        stats=DigestStats(
            folders_scanned=1,
            messages_parsed=2,
            dated_included=2,
            undated_needing_review=0,
            skipped_outside_window=0,
            skipped_seen_undated=0,
            deduped_messages=0,
            parse_errors=0,
            summaries_ollama=0,
            summaries_cache=0,
            summaries_fallback=0,
        ),
    )
    entries, collisions = flatten_report_entries(report)
    assert collisions == 1
    assert len(entries) == 1
    assert entries[0].display_position == 0


def test_manifest_backfill_null_counts(tmp_path: Path):
    db = tmp_path / "rollup.db"
    init_db(db).close()
    run_id = str(uuid.uuid4())
    manifest = {
        "schema_version": 2,
        "run_id": run_id,
        "started_at": format_utc(NOW),
        "completed_at": format_utc(NOW),
        "status": "success",
        "mode": "manual",
        "rollup_version": "0.5.0",
        "dated_outputs_written": True,
        "counts": {"messages_included": 3},
        "outputs": {"markdown": "a.md", "html": "a.html"},
        "window": {"lookback_days": 7},
    }
    assert backfill_run_from_manifest(
        db, manifest, state_dir=tmp_path, output_dir=tmp_path / "out"
    )
    conn = init_db(db)
    row = conn.execute(
        """SELECT stats_completeness, entry_index_version, messages_included,
                  messages_skipped_outside_window, summaries_ollama
           FROM rollup_runs WHERE run_id=?""",
        (run_id,),
    ).fetchone()
    assert row[0] == "manifest_partial"
    assert row[1] == 0
    assert row[2] == 3
    assert row[3] is None  # absent from manifest counts → NULL not 0
    assert row[4] is None
    conn.close()


def test_reject_absolute_and_dotdot_paths(tmp_path: Path):
    db = tmp_path / "rollup.db"
    run_id = str(uuid.uuid4())
    with pytest.raises(RunIndexError):
        index_rollup_run(
            db,
            _payload(
                run_id,
                [_entry("mid:a@x")],
                markdown_relpath="/etc/passwd",
                html_relpath="x.html",
            ),
        )
    # Relative path with ..
    with pytest.raises(RunIndexError):
        from rollup.run_index import _relative_path

        _relative_path(Path("../escape.md"), tmp_path / "out")


def test_relative_path_strips_output_prefix(tmp_path: Path, monkeypatch):
    from rollup.run_index import _relative_path

    out = tmp_path / "output"
    out.mkdir()
    artifact = out / "digest.md"
    artifact.write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # Relative path that includes the output dir name must resolve to basename.
    assert _relative_path(Path("output/digest.md"), Path("output")) == "digest.md"
    assert _relative_path(artifact, out) == "digest.md"


def test_reindex_relative_state_dir(tmp_path: Path, monkeypatch):
    from rollup.run_index import reindex_from_manifests

    monkeypatch.chdir(tmp_path)
    state = Path("state")
    out = Path("output")
    (state / "manifests").mkdir(parents=True)
    out.mkdir()
    (out / "a.md").write_text("md", encoding="utf-8")
    (out / "a.html").write_text("html", encoding="utf-8")
    run_id = str(uuid.uuid4())
    man = {
        "schema_version": 2,
        "run_id": run_id,
        "started_at": format_utc(NOW),
        "completed_at": format_utc(NOW),
        "status": "success",
        "mode": "manual",
        "dated_outputs_written": True,
        "counts": {"messages_included": 1},
        "outputs": {"markdown": "a.md", "html": "a.html"},
        "window": {"lookback_days": 7},
    }
    (state / "manifests" / "m.json").write_text(
        __import__("json").dumps(man), encoding="utf-8"
    )
    n = reindex_from_manifests(state / "rollup.db", state, out)
    assert n == 1
    conn = init_db(state / "rollup.db")
    row = conn.execute(
        "SELECT manifest_relpath, markdown_relpath FROM rollup_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    assert row[0] == "manifests/m.json"
    assert row[1] == "a.md"
    assert reindex_from_manifests(state / "rollup.db", state, out) == 0
    conn.close()


def test_ratings_survive_artifact_deletion(tmp_path: Path):
    db = tmp_path / "rollup.db"
    out = tmp_path / "out"
    out.mkdir()
    md = out / "x.md"
    html = out / "x.html"
    md.write_text("md", encoding="utf-8")
    html.write_text("html", encoding="utf-8")
    run_id = str(uuid.uuid4())
    index_rollup_run(
        db,
        _payload(run_id, [_entry("mid:keep@x")], markdown_relpath="x.md", html_relpath="x.html"),
    )
    conn = init_db(db)
    set_rating_with_reasons(conn, "mid:keep@x", 5, ["concise"], now=NOW)
    conn.close()
    md.unlink()
    html.unlink()
    conn = init_db(db)
    rating = get_rating(conn, "mid:keep@x")
    assert rating is not None and rating.stars == 5
    assert conn.execute(
        "SELECT COUNT(*) FROM message_source_links WHERE message_key=?",
        ("mid:keep@x",),
    ).fetchone()[0] == 1
    conn.close()
