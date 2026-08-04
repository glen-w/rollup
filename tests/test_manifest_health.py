"""Bounded Admin manifest health scanning."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from rollup.run_index import RunIndexPayload, index_rollup_run
from rollup.state import init_db
from rollup.utc import format_utc
from rollup.web.manifest_health import ManifestScanLimits, collect_manifest_health


def _index_run(db: Path, *, run_id: str, status: str, started: str) -> None:
    payload = RunIndexPayload(
        run_id=run_id,
        started_at=started,
        completed_at=started,
        status=status,
        mode="manual",
        rollup_version="0.6.0",
        manifest_schema_version=2,
        report_schema_version=1,
        stats_completeness="full",
        window_start=started,
        window_end=started,
        lookback_days=7,
        digest_fingerprint="fp",
        messages_included=0,
        messages_skipped_outside_window=0,
        messages_skipped_seen_undated=0,
        messages_deduped=0,
        messages_skipped_disabled_source=0,
        groups_created=0,
        sources_included=0,
        summaries_ollama=0,
        summaries_cache=0,
        summaries_fallback=0,
        summaries_errors=0,
        summaries_final_review_applied=0,
        group_summaries_succeeded=0,
        warning_count=0,
        degraded=False,
        manifest_relpath=None,
        markdown_relpath=None,
        html_relpath=None,
        index_source="pipeline",
        entries=[],
        expected_entry_count=0,
    )
    index_rollup_run(db, payload)


def _manifest(
    *,
    run_id: str,
    status: str,
    started: str,
    errors: list | None = None,
    warnings: list | None = None,
) -> dict:
    return {
        "schema_version": 2,
        "run_id": run_id,
        "started_at": started,
        "completed_at": started,
        "status": status,
        "mode": "cron",
        "rollup_version": "0.6.0",
        "counts": {
            "messages_seen": 1,
            "messages_parsed": 1,
            "messages_included": 1,
        },
        "dated_outputs_written": False,
        "latest_outputs_updated": False,
        "config_fingerprint": "abc123",
        "errors": errors or [],
        "warnings": warnings or [],
    }


def test_collect_indexed_only_when_no_manifest_dir(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    db = state / "rollup.db"
    init_db(db).close()
    run_id = str(uuid.uuid4())
    started = format_utc(datetime(2024, 6, 1, tzinfo=timezone.utc))
    _index_run(db, run_id=run_id, status="success", started=started)
    conn = init_db(db)
    panel = collect_manifest_health(conn, state_dir=state, output_dir=out)
    conn.close()
    assert panel.examined == 0
    assert panel.parsed == 0
    assert len(panel.cards) == 1
    assert panel.cards[0].run_id == run_id
    assert panel.cards[0].source == "indexed"
    assert "incomplete" in panel.incomplete_history_note.lower()


def test_collect_merges_manifest_and_flags_status_conflict(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    mdir = state / "manifests"
    mdir.mkdir()
    db = state / "rollup.db"
    init_db(db).close()
    run_id = str(uuid.uuid4())
    started = format_utc(datetime(2024, 6, 2, tzinfo=timezone.utc))
    _index_run(db, run_id=run_id, status="success", started=started)
    payload = _manifest(
        run_id=run_id,
        status="failure",
        started=started,
        errors=[
            {
                "code": "no_input",
                "message": "SECRET subject leak",
                "exception": "traceback",
            }
        ],
    )
    (mdir / f"{run_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    conn = init_db(db)
    panel = collect_manifest_health(conn, state_dir=state, output_dir=out)
    conn.close()
    assert panel.parsed == 1
    assert panel.skipped == 0
    card = panel.cards[0]
    assert card.source == "both"
    assert card.status == "success"  # indexed wins
    assert card.conflict_note is not None
    assert "failure" in card.conflict_note
    # Diagnostics redacted: codes only, no secret message/exception bodies.
    joined = " ".join(card.diagnostics)
    assert "no_input" in joined
    assert "SECRET" not in joined
    assert "traceback" not in joined
    # Recovery labels follow indexed (authoritative) status, so stay empty on success.


def test_collect_skips_malformed_and_oversized(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    mdir = state / "manifests"
    mdir.mkdir()
    db = state / "rollup.db"
    init_db(db).close()
    (mdir / "bad.json").write_text("{not-json", encoding="utf-8")
    (mdir / "huge.json").write_bytes(b"{" + b"x" * 2000)
    good_id = str(uuid.uuid4())
    started = "2024-06-03T00:00:00+00:00"
    (mdir / "good.json").write_text(
        json.dumps(_manifest(run_id=good_id, status="partial", started=started)),
        encoding="utf-8",
    )
    conn = init_db(db)
    panel = collect_manifest_health(
        conn,
        state_dir=state,
        output_dir=out,
        limits=ManifestScanLimits(max_dir_entries=50, max_files=10, max_bytes=500),
    )
    conn.close()
    assert panel.parsed == 1
    assert panel.skipped >= 2
    codes = {i.code for i in panel.issues}
    assert "manifest_malformed" in codes
    assert "manifest_oversized" in codes
    assert panel.cards[0].run_id == good_id
    assert panel.cards[0].source == "manifest"
    assert panel.cards[0].status == "partial"


def test_collect_failure_manifest_recovery_labels(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    mdir = state / "manifests"
    mdir.mkdir()
    db = state / "rollup.db"
    init_db(db).close()
    run_id = str(uuid.uuid4())
    payload = _manifest(
        run_id=run_id,
        status="failure",
        started="2024-06-04T00:00:00+00:00",
        errors=[{"code": "no_input", "folder": "tech"}],
    )
    (mdir / "fail.json").write_text(json.dumps(payload), encoding="utf-8")
    conn = init_db(db)
    panel = collect_manifest_health(conn, state_dir=state, output_dir=out)
    conn.close()
    assert panel.cards[0].status == "failure"
    assert "no_input" in panel.cards[0].recovery_labels


def test_collect_skips_symlink_manifest(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    mdir = state / "manifests"
    mdir.mkdir()
    db = state / "rollup.db"
    init_db(db).close()
    real = tmp_path / "outside.json"
    real.write_text("{}", encoding="utf-8")
    (mdir / "link.json").symlink_to(real)
    conn = init_db(db)
    panel = collect_manifest_health(conn, state_dir=state, output_dir=out)
    conn.close()
    assert any(i.code == "manifest_symlink" for i in panel.issues)
    assert panel.parsed == 0
