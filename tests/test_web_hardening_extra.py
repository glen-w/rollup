"""Concurrency, doctor web_index, packaging, and no-Flask smoke."""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.config import Config
from rollup.doctor import run_doctor
from rollup.run_index import IndexEntry, RunIndexPayload, index_rollup_run
from rollup.run_options import RunOptions
from rollup.state import BUSY_TIMEOUT_MS, connect_db, init_db
from rollup.utc import format_utc

NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _minimal_config(tmp_path: Path) -> Config:
    mail = tmp_path / "mail"
    mail.mkdir()
    return Config(
        root=tmp_path / "root",
        mail_root=mail,
        output_dir=tmp_path / "out",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        lookback_days=7,
        folders_include=(),
        folders_exclude=(),
        no_ollama=True,
        include_seen_undated=False,
        rebuild_summaries=False,
        max_body_chars=200_000,
        max_chars_for_llm=30_000,
        max_display_links=8,
        ollama_url="http://127.0.0.1:9/api/generate",
        ollama_model="none",
        allow_remote_ollama=False,
        summary_profile=None,
        summary_variants=(),
        summary_type_routing=None,
        summary_profile_set_path=None,
        export_summary_profile_set_path=None,
        list_summary_profiles=False,
        list_newsletter_types=False,
        summary_routing_report=False,
    )


def _index_one(tmp_path: Path) -> tuple[Path, str]:
    state = tmp_path / "state"
    out = tmp_path / "out"
    state.mkdir()
    out.mkdir()
    db = state / "rollup.db"
    init_db(db).close()
    run_id = str(uuid.uuid4())
    iso = format_utc(NOW)
    (out / "x.md").write_text("md", encoding="utf-8")
    (out / "x.html").write_text("html", encoding="utf-8")
    index_rollup_run(
        db,
        RunIndexPayload(
            run_id=run_id,
            started_at=iso,
            completed_at=iso,
            status="success",
            mode="manual",
            rollup_version="0.5.0",
            manifest_schema_version=None,
            report_schema_version=1,
            stats_completeness="full",
            window_start=iso,
            window_end=iso,
            lookback_days=7,
            digest_fingerprint="x",
            messages_included=1,
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
            entries=[
                IndexEntry(
                    message_key="mid:msg@x",
                    source_key_observed="from:a@example.com",
                    group_id=None,
                    group_type=None,
                    group_display_name=None,
                    section_key="tech",
                    section_position=0,
                    group_position=None,
                    entry_position=0,
                    display_position=0,
                    folder_name="tech",
                    subject="S",
                    sender="A",
                    date_parsed=iso,
                    date_raw="",
                    newsletter_type="essay",
                    summary="sum",
                    summary_source="none",
                    primary_link=None,
                    links_json='{"v":1,"items":[]}',
                )
            ],
            expected_entry_count=1,
        ),
    )
    return db, run_id


def test_doctor_web_index_pass_and_warn(tmp_path: Path):
    db, run_id = _index_one(tmp_path)
    config = _minimal_config(tmp_path)
    # paths already match fixture layout from _index_one
    report = run_doctor(config, RunOptions(dry_run=True))
    by_id = {c.id: c for c in report.checks}
    assert by_id["web_index"].status == "pass"

    (tmp_path / "out" / "x.md").unlink()
    report2 = run_doctor(config, RunOptions(dry_run=True))
    by_id2 = {c.id: c for c in report2.checks}
    assert by_id2["web_index"].status == "warn"


def test_busy_db_returns_503(tmp_path: Path, monkeypatch):
    flask = pytest.importorskip("flask")
    from rollup import state as state_mod
    from rollup.web.app import create_app
    from rollup.web_ids import encode_opaque

    monkeypatch.setattr(state_mod, "BUSY_TIMEOUT_MS", 50)

    db, run_id = _index_one(tmp_path)
    app = create_app(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "out",
        testing=True,
    )
    client = app.test_client()
    page = client.get(f"/rollups/{run_id}")
    m = re.search(rb'name="csrf_token" value="([^"]+)"', page.data)
    assert m
    token = m.group(1).decode()

    holder = connect_db(db)
    holder.execute(f"PRAGMA busy_timeout = {state_mod.BUSY_TIMEOUT_MS}")
    holder.execute("BEGIN IMMEDIATE")

    result: dict = {}

    def post():
        enc = encode_opaque("mid:msg@x")
        result["resp"] = client.post(
            f"/messages/{enc}/rating",
            data={
                "stars": "4",
                "csrf_token": token,
                "next": f"/rollups/{run_id}",
            },
        )

    t = threading.Thread(target=post)
    t.start()
    t.join(timeout=5)
    holder.rollback()
    holder.close()
    assert not t.is_alive()
    assert result["resp"].status_code == 503


def test_package_data_includes_web_assets():
    import rollup

    pkg = Path(rollup.__file__).resolve().parent
    assert (pkg / "web" / "templates" / "base.html").is_file()
    assert (pkg / "web" / "static" / "web.css").is_file()


def test_rollup_web_exits_without_flask(tmp_path: Path):
    """Subprocess: env without Flask must exit 1 with install hint."""
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    pip = venv / "bin" / "pip"
    rollup_bin = venv / "bin" / "rollup"
    subprocess.run(
        [str(pip), "install", "-q", "-e", str(ROOT)],
        check=True,
    )
    subprocess.run([str(pip), "uninstall", "-y", "flask"], check=False)
    proc = subprocess.run(
        [str(rollup_bin), "web", "--port", "18770"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "pip install 'rollup[web]'" in proc.stderr
    proc2 = subprocess.run(
        [str(rollup_bin), "digest", "--help"],
        capture_output=True,
        text=True,
    )
    assert proc2.returncode == 0
