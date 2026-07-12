"""Non-blocking advisory file lock for single-run digest execution."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

DEFAULT_LOCK_TTL_SECONDS = 6 * 60 * 60  # 6 hours
LOCK_FILENAME = "rollup.lock"


class RunLockError(Exception):
    """Raised when a state operation cannot acquire the process lock."""

    def __init__(
        self,
        message: str,
        *,
        reason: Literal["already_running"] = "already_running",
        other_run_id: str | None = None,
        other_operation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.other_run_id = other_run_id
        self.other_operation = other_operation


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we cannot signal it.
        return True
    except OSError:
        return False
    return True


def _read_lock_payload(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _is_stale(payload: dict | None, *, ttl_seconds: int) -> bool:
    if payload is None:
        return True
    pid = int(payload.get("pid") or 0)
    if not _pid_alive(pid):
        return True
    started = payload.get("started_at")
    if not started:
        return True
    try:
        started_at = datetime.fromisoformat(started)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - started_at.astimezone(timezone.utc)).total_seconds()
        return age > ttl_seconds
    except (TypeError, ValueError):
        return True


@dataclass
class RunLock:
    """Held advisory lock; call release() in finally."""

    state_dir: Path
    lock_path: Path
    run_id: str
    fd: int | None
    stale_recovered: bool = False
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        if self.fd is not None:
            try:
                _unlock_fd(self.fd)
            except OSError as exc:
                logger.warning("Could not unlock %s: %s", self.lock_path, exc)
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        try:
            if self.lock_path.exists():
                payload = _read_lock_payload(self.lock_path)
                if payload and payload.get("run_id") == self.run_id:
                    self.lock_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove lock file %s: %s", self.lock_path, exc)


def _lock_fd(fd: int) -> None:
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    except ImportError:
        pass
    # Windows fallback
    import msvcrt

    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)


def _unlock_fd(fd: int) -> None:
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    except ImportError:
        pass
    import msvcrt

    try:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


def acquire_state_lock(
    state_dir: Path,
    run_id: str,
    *,
    operation: str = "digest",
    started_at: datetime | None = None,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
) -> RunLock:
    """Acquire a non-blocking state-operation lock under state_dir."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / LOCK_FILENAME
    started = started_at or datetime.now().astimezone()
    stale_recovered = False

    if lock_path.exists():
        payload = _read_lock_payload(lock_path)
        if not _is_stale(payload, ttl_seconds=ttl_seconds):
            other = (payload or {}).get("run_id", "unknown")
            other_op = (payload or {}).get("operation", "digest")
            raise RunLockError(
                f"ERROR: Another state operation is in progress "
                f"(operation={other_op}, run_id={other})",
                reason="already_running",
                other_run_id=str(other),
                other_operation=str(other_op),
            )
        try:
            lock_path.unlink(missing_ok=True)
            stale_recovered = True
            logger.warning("Recovered stale run lock at %s", lock_path)
        except OSError:
            pass

    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        _lock_fd(fd)
    except (BlockingIOError, OSError) as exc:
        os.close(fd)
        payload = _read_lock_payload(lock_path)
        other = (payload or {}).get("run_id", "unknown")
        other_op = (payload or {}).get("operation", "digest")
        raise RunLockError(
            f"ERROR: Another state operation is in progress "
            f"(operation={other_op}, run_id={other})",
            reason="already_running",
            other_run_id=str(other),
            other_operation=str(other_op),
        ) from exc

    payload = {
        "pid": os.getpid(),
        "run_id": run_id,
        "operation": operation,
        "started_at": started.isoformat(),
        "acquired_at": time.time(),
    }
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, json.dumps(payload, indent=2).encode("utf-8"))
    try:
        os.fsync(fd)
    except OSError:
        pass

    return RunLock(
        state_dir=state_dir,
        lock_path=lock_path,
        run_id=run_id,
        fd=fd,
        stale_recovered=stale_recovered,
    )


def acquire_run_lock(
    state_dir: Path,
    run_id: str,
    *,
    started_at: datetime | None = None,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
) -> RunLock:
    """Backward-compatible alias for digest lock acquisition."""
    return acquire_state_lock(
        state_dir,
        run_id,
        operation="digest",
        started_at=started_at,
        ttl_seconds=ttl_seconds,
    )
