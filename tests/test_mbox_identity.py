"""Mbox identity snapshot and mid-run mutation classification."""

from __future__ import annotations

from pathlib import Path

from rollup.mbox_identity import (
    MboxIdentity,
    classify_mbox_mutation,
    snapshot_mbox,
)
from rollup.run_contracts import (
    MBOX_ANOMALY_DISAPPEARED,
    MBOX_ANOMALY_GREW,
    MBOX_ANOMALY_IDENTITY_WEAK,
    MBOX_ANOMALY_REPLACED,
    MBOX_ANOMALY_SHRUNK,
)


def test_snapshot_mbox_missing(tmp_path: Path) -> None:
    assert snapshot_mbox(tmp_path / "nope") is None


def test_snapshot_mbox_reads_size_and_identity(tmp_path: Path) -> None:
    path = tmp_path / "folder"
    path.write_bytes(b"abc")
    snap = snapshot_mbox(path)
    assert snap is not None
    assert snap.path == path
    assert snap.size == 3
    assert snap.mtime_ns > 0
    assert snap.st_dev is not None
    assert snap.st_ino is not None
    assert snap.identity_weak is False


def test_classify_unchanged() -> None:
    before = MboxIdentity(
        path=Path("a"),
        size=10,
        mtime_ns=100,
        st_dev=1,
        st_ino=2,
        identity_weak=False,
    )
    assert classify_mbox_mutation(before, before) is None
    assert classify_mbox_mutation(None, None) is None


def test_classify_disappeared() -> None:
    before = MboxIdentity(
        path=Path("a"),
        size=10,
        mtime_ns=100,
        st_dev=1,
        st_ino=2,
        identity_weak=False,
    )
    assert classify_mbox_mutation(before, None) == MBOX_ANOMALY_DISAPPEARED


def test_classify_appeared_as_replaced() -> None:
    after = MboxIdentity(
        path=Path("a"),
        size=10,
        mtime_ns=100,
        st_dev=1,
        st_ino=2,
        identity_weak=False,
    )
    assert classify_mbox_mutation(None, after) == MBOX_ANOMALY_REPLACED


def test_classify_dev_ino_replaced() -> None:
    before = MboxIdentity(
        path=Path("a"), size=10, mtime_ns=100, st_dev=1, st_ino=2, identity_weak=False
    )
    after = MboxIdentity(
        path=Path("a"), size=10, mtime_ns=100, st_dev=1, st_ino=99, identity_weak=False
    )
    assert classify_mbox_mutation(before, after) == MBOX_ANOMALY_REPLACED


def test_classify_grew_and_shrunk() -> None:
    before = MboxIdentity(
        path=Path("a"), size=10, mtime_ns=100, st_dev=1, st_ino=2, identity_weak=False
    )
    grew = MboxIdentity(
        path=Path("a"), size=20, mtime_ns=100, st_dev=1, st_ino=2, identity_weak=False
    )
    shrunk = MboxIdentity(
        path=Path("a"), size=5, mtime_ns=100, st_dev=1, st_ino=2, identity_weak=False
    )
    assert classify_mbox_mutation(before, grew) == MBOX_ANOMALY_GREW
    assert classify_mbox_mutation(before, shrunk) == MBOX_ANOMALY_SHRUNK


def test_classify_same_size_mtime_change_is_replaced() -> None:
    before = MboxIdentity(
        path=Path("a"), size=10, mtime_ns=100, st_dev=1, st_ino=2, identity_weak=False
    )
    after = MboxIdentity(
        path=Path("a"), size=10, mtime_ns=200, st_dev=1, st_ino=2, identity_weak=False
    )
    assert classify_mbox_mutation(before, after) == MBOX_ANOMALY_REPLACED


def test_classify_identity_weak_still_detects_size_and_mtime() -> None:
    before = MboxIdentity(
        path=Path("a"), size=10, mtime_ns=100, st_dev=None, st_ino=None, identity_weak=True
    )
    grew = MboxIdentity(
        path=Path("a"), size=11, mtime_ns=100, st_dev=None, st_ino=None, identity_weak=True
    )
    mtime = MboxIdentity(
        path=Path("a"), size=10, mtime_ns=200, st_dev=None, st_ino=0, identity_weak=True
    )
    # Size/mtime outcomes still win; weak flag does not suppress detection.
    assert classify_mbox_mutation(before, grew) == MBOX_ANOMALY_GREW
    assert classify_mbox_mutation(before, mtime) == MBOX_ANOMALY_REPLACED
    assert classify_mbox_mutation(before, before) is None
    # Constant remains part of the public anomaly vocabulary.
    assert MBOX_ANOMALY_IDENTITY_WEAK.startswith("mbox_")
