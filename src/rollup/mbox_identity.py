"""Mbox file identity snapshots for mutation detection during a run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rollup.run_contracts import (
    MBOX_ANOMALY_DISAPPEARED,
    MBOX_ANOMALY_GREW,
    MBOX_ANOMALY_IDENTITY_WEAK,
    MBOX_ANOMALY_REPLACED,
    MBOX_ANOMALY_SHRUNK,
)


@dataclass(frozen=True)
class MboxIdentity:
    path: Path
    size: int
    mtime_ns: int
    st_dev: int | None
    st_ino: int | None
    identity_weak: bool


def snapshot_mbox(path: Path) -> MboxIdentity | None:
    """Capture size/mtime_ns/dev/ino. Returns None if the file is missing."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    st_dev = getattr(st, "st_dev", None)
    st_ino = getattr(st, "st_ino", None)
    weak = st_dev is None or st_ino is None or st_ino == 0
    return MboxIdentity(
        path=path,
        size=int(st.st_size),
        mtime_ns=int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))),
        st_dev=int(st_dev) if st_dev is not None else None,
        st_ino=int(st_ino) if st_ino is not None else None,
        identity_weak=weak,
    )


def classify_mbox_mutation(
    before: MboxIdentity | None, after: MboxIdentity | None
) -> str | None:
    """Return an anomaly code when the mbox changed during parse, else None."""
    if before is None and after is None:
        return MBOX_ANOMALY_DISAPPEARED
    if before is not None and after is None:
        return MBOX_ANOMALY_DISAPPEARED
    if before is None and after is not None:
        return MBOX_ANOMALY_REPLACED
    assert before is not None and after is not None
    codes: list[str] = []
    if before.identity_weak or after.identity_weak:
        # Weak identity: still detect size/mtime changes.
        if before.size != after.size or before.mtime_ns != after.mtime_ns:
            codes.append(MBOX_ANOMALY_IDENTITY_WEAK)
    else:
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            return MBOX_ANOMALY_REPLACED
    if after.size < before.size:
        return MBOX_ANOMALY_SHRUNK
    if after.size > before.size:
        return MBOX_ANOMALY_GREW
    if before.mtime_ns != after.mtime_ns:
        # Same size, new mtime — treat as replaced content.
        return MBOX_ANOMALY_REPLACED
    if codes:
        return codes[0]
    return None
