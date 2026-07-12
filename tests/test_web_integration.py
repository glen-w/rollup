"""End-to-end: digest indexes into SQLite for web browsing."""

from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from rollup.clock import FixedClock
from rollup.config import Config
from rollup.pipeline import run_digest
from rollup.run_options import GroupingConfig, RunOptions
from rollup.state import init_db

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def _config(tmp_path: Path) -> Config:
    root = tmp_path / "Newsletters.sbd"
    root.mkdir()
    msg = EmailMessage()
    msg["Subject"] = "Web index newsletter"
    msg["From"] = "Sender <sender@example.com>"
    msg["To"] = "reader@example.com"
    msg["Message-ID"] = "<web-index@example.com>"
    msg["Date"] = format_datetime(NOW)
    msg.set_content("Body for web indexing.")
    mbox = root / "tech"
    mbox.write_text(
        "From - Sun Jul 12 12:00:00 2026\n" + msg.as_string() + "\n",
        encoding="utf-8",
    )
    mail_root = tmp_path / "mail"
    mail_root.mkdir()
    return Config(
        root=root,
        mail_root=mail_root,
        output_dir=tmp_path / "output",
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
        ollama_url="http://localhost:11434/api/generate",
        ollama_model="llama3.2:3b",
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


def test_digest_indexes_run_for_web(tmp_path: Path):
    config = _config(tmp_path)
    result = run_digest(
        config,
        RunOptions(write_manifest=True),
        grouping=GroupingConfig(enabled=False),
        clock=FixedClock(NOW),
    )
    assert result.status in ("success", "partial")
    conn = init_db(config.db_path)
    row = conn.execute(
        "SELECT run_id, entry_index_version, messages_included FROM rollup_runs"
    ).fetchone()
    assert row is not None
    assert int(row[1]) >= 1
    assert conn.execute("SELECT COUNT(*) FROM rollup_entries").fetchone()[0] >= 1
    assert conn.execute(
        "SELECT COUNT(*) FROM message_source_links"
    ).fetchone()[0] >= 1
    conn.close()


def test_digest_indexes_without_manifest(tmp_path: Path):
    config = _config(tmp_path)
    result = run_digest(
        config,
        RunOptions(write_manifest=False),
        grouping=GroupingConfig(enabled=False),
        clock=FixedClock(NOW),
    )
    assert result.status in ("success", "partial")
    conn = init_db(config.db_path)
    assert conn.execute("SELECT COUNT(*) FROM rollup_runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM rollup_entries").fetchone()[0] >= 1
    conn.close()


def test_dry_run_does_not_index(tmp_path: Path):
    config = _config(tmp_path)
    result = run_digest(
        config,
        RunOptions(dry_run=True, write_manifest=False),
        grouping=GroupingConfig(enabled=False),
        clock=FixedClock(NOW),
    )
    assert result.status == "dry_run"
    assert not config.db_path.exists()
