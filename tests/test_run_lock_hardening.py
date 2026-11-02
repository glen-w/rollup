"""Run-lock edge cases: stale PIDs, TTL expiry, corrupt payloads, contention, doctor."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rollup.run_lock import (
    LOCK_FILENAME,
    RunLockError,
    acquire_state_lock,
    inspect_state_lock,
)


def _write_lock(state_dir: Path, **payload) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / LOCK_FILENAME
    base = {
        "pid": os.getpid(),
        "run_id": "other-run",
        "operation": "digest",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "acquired_at": time.time(),
    }
    base.update(payload)
    path.write_text(json.dumps(base), encoding="utf-8")
    return path


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_live_fresh_lock_is_contended(tmp_path: Path) -> None:
    _write_lock(tmp_path, run_id="busy", operation="web-source-suggest")
    with pytest.raises(RunLockError) as info:
        acquire_state_lock(tmp_path, run_id="mine")
    assert info.value.reason == "already_running"
    assert info.value.other_run_id == "busy"
    assert info.value.other_operation == "web-source-suggest"
    # Contention must not clobber the holder's file.
    assert json.loads((tmp_path / LOCK_FILENAME).read_text())["run_id"] == "busy"


def test_dead_pid_lock_is_recovered(tmp_path: Path) -> None:
    _write_lock(tmp_path, pid=_dead_pid(), run_id="crashed")
    lock = acquire_state_lock(tmp_path, run_id="mine")
    try:
        assert lock.stale_recovered is True
        assert json.loads(lock.lock_path.read_text())["run_id"] == "mine"
    finally:
        lock.release()
    assert not lock.lock_path.exists()


def test_ttl_expired_live_pid_is_taken_over(tmp_path: Path) -> None:
    old = datetime.now(timezone.utc) - timedelta(hours=7)
    _write_lock(tmp_path, started_at=old.isoformat())
    lock = acquire_state_lock(tmp_path, run_id="mine", ttl_seconds=6 * 3600)
    try:
        assert lock.stale_recovered is True
    finally:
        lock.release()


def test_ttl_not_expired_live_pid_is_contended(tmp_path: Path) -> None:
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    _write_lock(tmp_path, started_at=recent.isoformat())
    with pytest.raises(RunLockError):
        acquire_state_lock(tmp_path, run_id="mine", ttl_seconds=6 * 3600)


@pytest.mark.parametrize(
    "content",
    ["", "not json", "{}", "[]", "42", '{"pid": "abc"}', '{"pid": 0, "run_id": "x"}', "\x00\x01"],
)
def test_corrupt_or_empty_payload_is_treated_as_stale(tmp_path: Path, content: str) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / LOCK_FILENAME).write_text(content, encoding="utf-8")
    try:
        lock = acquire_state_lock(tmp_path, run_id="mine")
    except ValueError:
        pytest.fail("corrupt payload must not raise ValueError")
    try:
        assert lock.stale_recovered is True
    finally:
        lock.release()


def test_naive_started_at_is_treated_as_utc(tmp_path: Path) -> None:
    naive_recent = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    _write_lock(tmp_path, started_at=naive_recent)
    with pytest.raises(RunLockError):
        acquire_state_lock(tmp_path, run_id="mine")


def test_release_is_idempotent_and_only_removes_own_file(tmp_path: Path) -> None:
    lock = acquire_state_lock(tmp_path, run_id="mine")
    lock.release()
    lock.release()  # no error
    assert not (tmp_path / LOCK_FILENAME).exists()

    # A different holder's file must survive a stale handle's release().
    _write_lock(tmp_path, run_id="someone-else")
    lock.release()
    assert (tmp_path / LOCK_FILENAME).exists()


def test_cross_process_contention(tmp_path: Path) -> None:
    holder = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / "src")!r})
        from rollup.run_lock import acquire_state_lock
        lock = acquire_state_lock({str(tmp_path)!r}, run_id="holder")
        print("held", flush=True)
        time.sleep(20)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", holder], stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(RunLockError) as info:
            acquire_state_lock(tmp_path, run_id="mine")
        assert info.value.other_run_id == "holder"
        inspection = inspect_state_lock(tmp_path)
        assert inspection is not None
        assert inspection.pid == proc.pid
        assert inspection.pid_alive is True
        assert inspection.stale is False
    finally:
        proc.kill()
        proc.wait()
    # Holder died without releasing: the next acquire recovers it.
    lock = acquire_state_lock(tmp_path, run_id="after")
    try:
        assert lock.stale_recovered is True
    finally:
        lock.release()


def test_inspect_absent_and_present(tmp_path: Path) -> None:
    assert inspect_state_lock(tmp_path) is None
    _write_lock(tmp_path, pid=_dead_pid(), run_id="gone", operation="backfill")
    inspection = inspect_state_lock(tmp_path)
    assert inspection is not None
    assert inspection.run_id == "gone"
    assert inspection.operation == "backfill"
    assert inspection.pid_alive is False
    assert inspection.stale is True


def test_doctor_reports_stale_and_held_locks(tmp_path: Path) -> None:
    from rollup.doctor import _check_run_lock
    from test_hardening_contracts import _config

    config = _config(tmp_path)
    assert _check_run_lock(config).status == "pass"

    _write_lock(config.state_dir, pid=_dead_pid())
    stale = _check_run_lock(config)
    assert stale.status in ("warn", "fail")
    assert "Stale" in stale.message
    assert stale.fix is not None

    _write_lock(config.state_dir)  # live pid, fresh
    held = _check_run_lock(config)
    assert "held" in held.message.lower()
