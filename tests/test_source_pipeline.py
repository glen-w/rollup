"""Pipeline integration for source registry."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rollup.clock import FixedClock
from rollup.config import Config
from rollup.pipeline import run_digest
from rollup.run_options import GroupingConfig, RunOptions
from rollup.source_registry import set_overrides
from rollup.state import init_db

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "Newsletters.sbd"
NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def _config(tmp_path: Path) -> Config:
    mail_root = tmp_path / "mail"
    mail_root.mkdir(exist_ok=True)
    return Config(
        root=FIXTURE_ROOT,
        mail_root=mail_root,
        output_dir=tmp_path / "out",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        lookback_days=3650,
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


def test_dry_run_no_db(tmp_path: Path) -> None:
    config = _config(tmp_path)
    result = run_digest(
        config,
        RunOptions(dry_run=True),
        grouping=GroupingConfig(enabled=False),
        clock=FixedClock(NOW),
    )
    assert result.status == "dry_run"
    assert not (config.state_dir / "rollup.db").exists()


def test_disabled_source_excluded(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.state_dir.mkdir(parents=True)
    run_digest(
        config,
        RunOptions(dry_run=False, write_manifest=False),
        grouping=GroupingConfig(enabled=False),
        clock=FixedClock(NOW),
        acquire_lock=False,
    )
    conn = init_db(config.db_path)
    keys = [
        r[0]
        for r in conn.execute(
            "SELECT source_key FROM sources WHERE lifecycle='active'"
        ).fetchall()
    ]
    assert keys
    for key in keys:
        set_overrides(conn, key, updates={"enabled": False})
    conn.close()

    result = run_digest(
        config,
        RunOptions(dry_run=False, write_manifest=False),
        grouping=GroupingConfig(enabled=False),
        clock=FixedClock(NOW),
        acquire_lock=False,
    )
    assert result.status == "success"
    assert result.report is not None
    total = sum(len(v) for v in result.report.dated_by_folder.values()) + len(
        result.report.undated
    )
    assert total == 0
    assert result.aggregated.filter.counts.skipped_disabled_source >= 1
