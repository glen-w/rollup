"""Side-effect-free doctor checks used by Admin GET."""

from __future__ import annotations

from pathlib import Path

from rollup.doctor_readonly import (
    foreign_key_check_bounded,
    run_doctor_readonly,
    schema_panel_snapshot,
)
from rollup.state import SCHEMA_VERSION, init_db


def test_run_doctor_readonly_ok(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "out"
    mail = tmp_path / "mail"
    news = tmp_path / "news"
    logs = tmp_path / "logs"
    for p in (state, out, mail, news, logs):
        p.mkdir()
    (state / "manifests").mkdir()
    conn = init_db(state / "rollup.db")
    report = run_doctor_readonly(
        conn,
        state_dir=state,
        output_dir=out,
        mail_root=mail,
        newsletter_root=news,
        log_dir=logs,
    )
    conn.close()
    assert report.ok
    assert report.error_count == 0
    ids = {c.id for c in report.checks}
    assert "package_version" in ids
    assert "schema_version" in ids
    assert "canonical_tables" in ids
    assert "manifest_dir" in ids
    assert "log_exists" in ids
    schema = next(c for c in report.checks if c.id == "schema_version")
    assert schema.status == "pass"
    assert str(SCHEMA_VERSION) in schema.message


def test_run_doctor_readonly_warns_unconfigured_and_missing(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    conn = init_db(state / "rollup.db")
    report = run_doctor_readonly(
        conn,
        state_dir=state,
        output_dir=None,
        mail_root=tmp_path / "missing-mail",
        newsletter_root=None,
    )
    conn.close()
    by_id = {c.id: c for c in report.checks}
    assert by_id["output_exists"].status == "warn"
    assert by_id["newsletter_root_exists"].status == "warn"
    assert by_id["mail_root_exists"].status == "fail"
    assert by_id["manifest_dir"].status == "info"
    assert not report.ok
    assert report.error_count >= 1


def test_run_doctor_readonly_rejects_file_as_dir(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    not_dir = tmp_path / "output.txt"
    not_dir.write_text("x", encoding="utf-8")
    conn = init_db(state / "rollup.db")
    report = run_doctor_readonly(conn, state_dir=state, output_dir=not_dir)
    conn.close()
    out = next(c for c in report.checks if c.id == "output_exists")
    assert out.status == "fail"
    assert "not a directory" in out.message


def test_run_doctor_readonly_rejects_manifest_symlink(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    (state / "manifests").symlink_to(target)
    conn = init_db(state / "rollup.db")
    report = run_doctor_readonly(conn, state_dir=state, output_dir=None)
    conn.close()
    m = next(c for c in report.checks if c.id == "manifest_dir")
    assert m.status == "fail"


def test_schema_panel_snapshot(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    snap = schema_panel_snapshot(conn)
    conn.close()
    assert snap["error"] is None
    assert snap["schema_version"] == SCHEMA_VERSION
    assert snap["package_schema_version"] == SCHEMA_VERSION
    assert snap["missing_tables"] == []
    assert isinstance(snap["journal_mode"], str)
    assert snap["journal_mode"]


def test_foreign_key_check_bounded_clean(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    result = foreign_key_check_bounded(conn, limit=10)
    conn.close()
    assert result["error"] is None
    assert result["count"] == 0
    assert result["sample"] == []
    assert result["truncated"] is False
