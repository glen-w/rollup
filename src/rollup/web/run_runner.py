"""Synchronous digest subprocess runner for Run Studio (not a scheduler)."""

from __future__ import annotations

import collections
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


@dataclass
class ActiveRun:
    """In-memory single-slot run state for the web process."""

    run_id: str
    argv: list[str]
    dry_run: bool
    started_at: float
    status: str = "running"  # running | success | partial | failure | dry_run
    exit_code: int | None = None
    log_lines: collections.deque[str] = field(
        default_factory=lambda: collections.deque(maxlen=400)
    )
    finished_at: float | None = None
    error: str | None = None


_lock = threading.Lock()
_active: ActiveRun | None = None
_process: subprocess.Popen[str] | None = None


def get_active_run() -> ActiveRun | None:
    with _lock:
        return _active


def is_busy() -> bool:
    with _lock:
        return _active is not None and _active.status == "running"


def _append_log(run: ActiveRun, line: str) -> None:
    text = line.rstrip("\n")
    if text:
        run.log_lines.append(text)


def _reader(proc: subprocess.Popen[str], run: ActiveRun) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        _append_log(run, line)


def start_digest_subprocess(
    argv: Sequence[str],
    *,
    dry_run: bool,
    cwd: Path | None = None,
) -> ActiveRun:
    """Start `python -m rollup …` in a subprocess. Raises RuntimeError if busy."""
    global _active, _process
    with _lock:
        if _active is not None and _active.status == "running":
            raise RuntimeError("A digest is already running")
        run_id = f"web-{int(time.time())}"
        full_argv = [sys.executable, "-m", "rollup", *argv]
        run = ActiveRun(
            run_id=run_id,
            argv=list(full_argv),
            dry_run=dry_run,
            started_at=time.time(),
        )
        try:
            proc = subprocess.Popen(
                full_argv,
                cwd=str(cwd) if cwd else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            run.status = "failure"
            run.exit_code = 1
            run.error = str(exc)
            run.finished_at = time.time()
            _active = run
            _process = None
            return run
        _process = proc
        _active = run
        thread = threading.Thread(target=_reader, args=(proc, run), daemon=True)
        thread.start()
        waiter = threading.Thread(
            target=_wait_proc, args=(proc, run, thread), daemon=True
        )
        waiter.start()
        return run


def _wait_proc(
    proc: subprocess.Popen[str],
    run: ActiveRun,
    reader: threading.Thread,
) -> None:
    global _process
    code = proc.wait()
    reader.join(timeout=5)
    with _lock:
        run.exit_code = code
        run.finished_at = time.time()
        if run.dry_run and code == 0:
            run.status = "dry_run"
        elif code == 0:
            run.status = "success"
        elif code == 2:
            run.status = "partial"
        else:
            run.status = "failure"
        if _process is proc:
            _process = None


def wait_until_idle(*, timeout: float = 3600.0) -> ActiveRun | None:
    """Block until the active run finishes (for synchronous form POSTs)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _lock:
            run = _active
            if run is None or run.status != "running":
                return run
        time.sleep(0.25)
    return get_active_run()


def clear_finished_for_tests() -> None:
    global _active, _process
    with _lock:
        _active = None
        _process = None
