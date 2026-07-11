"""Tests for run lock, status derivation, clock, and fs helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rollup.clock import FixedClock
from rollup.fsutil import atomic_write_text, publish_file_set
from rollup.pipeline import (
    AggregatedResults,
    ParseCounts,
    ParseResult,
    derive_run_status,
    status_to_exit_code,
)
from rollup.run_context import RunContext
from rollup.run_lock import RunLockError, acquire_run_lock
from rollup.run_options import resolve_run_options


def test_fixed_clock_and_run_context() -> None:
    instant = datetime(2026, 7, 10, 9, 0, 0, tzinfo=timezone.utc)
    clock = FixedClock(instant)
    ctx = RunContext.create(mode="cron", clock=clock, run_id="abcd-1234")
    assert ctx.run_start_time == instant
    assert ctx.mode == "cron"
    assert ctx.run_id_short == "abcd1234"
    ctx.add_event("test", "hello", level="warning")
    assert len(ctx.events) == 1


def test_resolve_run_options_cron_defaults() -> None:
    opts = resolve_run_options(cron=True)
    assert opts.mode == "cron"
    assert opts.quiet is True
    assert opts.publish_latest is True

    opts2 = resolve_run_options(cron=True, verbose=True)
    assert opts2.quiet is False

    opts3 = resolve_run_options(cron=True, publish_latest=False)
    assert opts3.publish_latest is False


def test_atomic_write_and_publish_file_set(tmp_path: Path) -> None:
    src = tmp_path / "a.md"
    atomic_write_text(src, "hello")
    assert src.read_text() == "hello"
    dest = tmp_path / "latest.md"
    publish_file_set([(src, dest)])
    assert dest.read_text() == "hello"


def test_run_lock_blocks_second_acquisition(tmp_path: Path) -> None:
    lock = acquire_run_lock(tmp_path, "run-1")
    try:
        with pytest.raises(RunLockError) as excinfo:
            acquire_run_lock(tmp_path, "run-2")
        assert excinfo.value.reason == "already_running"
        assert "run-1" in str(excinfo.value)
    finally:
        lock.release()


def test_run_lock_stale_recovery(tmp_path: Path) -> None:
    lock_path = tmp_path / "rollup.lock"
    stale = {
        "pid": 999999999,
        "run_id": "dead-run",
        "started_at": (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(),
    }
    lock_path.write_text(json.dumps(stale), encoding="utf-8")
    lock = acquire_run_lock(tmp_path, "fresh-run", ttl_seconds=3600)
    assert lock.stale_recovered is True
    lock.release()
    assert not lock_path.exists()


def test_derive_run_status_thresholds() -> None:
    agg = AggregatedResults(usable_digest=True)
    assert derive_run_status(agg) == "success"
    assert status_to_exit_code("success") == 0
    assert status_to_exit_code("partial") == 2
    assert status_to_exit_code("failure") == 1

    agg.hard_failure = True
    assert derive_run_status(agg) == "failure"

    agg = AggregatedResults(
        usable_digest=True,
        parse=ParseResult(
            messages=(),
            counts=ParseCounts(
                messages_seen=100,
                messages_parsed=80,
                parse_fatal_errors=20,
                folders_failed=0,
            ),
        ),
    )
    assert derive_run_status(agg) == "partial"

    agg = AggregatedResults(
        usable_digest=True,
        parse=ParseResult(
            messages=(),
            counts=ParseCounts(folders_failed=1, messages_seen=10, messages_parsed=10),
        ),
    )
    assert derive_run_status(agg) == "partial"

    agg = AggregatedResults(usable_digest=True)
    assert derive_run_status(agg, dry_run=True) == "dry_run"
