"""Tests for run lock, status derivation, clock, and fs helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rollup.clock import FixedClock
from rollup.fsutil import atomic_copy, atomic_write_bytes, atomic_write_text, publish_file_set
from rollup.pipeline import (
    AggregatedResults,
    ParseCounts,
    ParseResult,
    derive_run_status,
    status_to_exit_code,
)
from rollup.run_context import RunContext
from rollup.run_lock import RunLockError, acquire_run_lock, acquire_state_lock
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


def test_atomic_write_bytes_and_copy(tmp_path: Path) -> None:
    nested = tmp_path / "sub" / "bin.dat"
    atomic_write_bytes(nested, b"\x00\x01\xff")
    assert nested.read_bytes() == b"\x00\x01\xff"
    dest = tmp_path / "copy" / "bin.dat"
    atomic_copy(nested, dest)
    assert dest.read_bytes() == b"\x00\x01\xff"


def test_publish_file_set_rollback_on_failure(tmp_path: Path, monkeypatch) -> None:
    src_a = tmp_path / "a.md"
    src_b = tmp_path / "b.md"
    atomic_write_text(src_a, "A-new")
    atomic_write_text(src_b, "B-new")
    dest_a = tmp_path / "latest.md"
    dest_b = tmp_path / "latest.html"
    atomic_write_text(dest_a, "A-old")
    atomic_write_text(dest_b, "B-old")

    original_replace = Path.replace
    calls = {"n": 0}

    def flaky_replace(self: Path, target: Path):
        # Allow staging backups and first commit; fail on second commit rename.
        calls["n"] += 1
        # Commit phase renames temps onto destinations (names start with .tmp-).
        if self.name.startswith(".tmp-") and calls["n"] >= 4:
            raise OSError("simulated commit failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    with pytest.raises(OSError, match="simulated"):
        publish_file_set([(src_a, dest_a), (src_b, dest_b)])
    assert dest_a.read_text() == "A-old"
    assert dest_b.read_text() == "B-old"
    assert not list(tmp_path.glob(".tmp-*"))
    assert not list(tmp_path.glob(".bak-*"))


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


def test_state_lock_operation_in_payload(tmp_path: Path) -> None:
    lock = acquire_state_lock(tmp_path, "src-1", operation="sources_set")
    try:
        payload = json.loads(lock.lock_path.read_text(encoding="utf-8"))
        assert payload["operation"] == "sources_set"
        assert payload["run_id"] == "src-1"
        with pytest.raises(RunLockError) as excinfo:
            acquire_state_lock(tmp_path, "src-2", operation="sources_import")
        assert excinfo.value.other_operation == "sources_set"
        assert "sources_set" in str(excinfo.value)
    finally:
        lock.release()


def test_corrupt_lock_stale_recovery(tmp_path: Path) -> None:
    lock_path = tmp_path / "rollup.lock"
    lock_path.write_text("{not-json", encoding="utf-8")
    lock = acquire_run_lock(tmp_path, "after-corrupt")
    assert lock.stale_recovered is True
    lock.release()
    lock.release()  # idempotent
    assert not lock_path.exists()


def test_live_pid_lock_blocks_even_when_fresh(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("rollup.run_lock._pid_alive", lambda pid: True)
    lock_path = tmp_path / "rollup.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": 1,
                "run_id": "alive",
                "operation": "digest",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RunLockError) as excinfo:
        acquire_run_lock(tmp_path, "blocked", ttl_seconds=3600)
    assert excinfo.value.reason == "already_running"


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
