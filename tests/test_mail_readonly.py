"""Ensure inventory and digest never modify the mail fixture tree."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "Newsletters.sbd"
PROJECT_ROOT = Path(__file__).parent.parent

FORBIDDEN_SUFFIXES = (".lock", ".tmp", ".db", ".log", ".dotlock")


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    """Map relative path -> (size, mtime_ns)."""
    snap: dict[str, tuple[int, int]] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        for name in filenames:
            path = base / name
            rel = str(path.relative_to(root))
            stat = path.stat()
            snap[rel] = (stat.st_size, stat.st_mtime_ns)
    return snap


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "rollup", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def test_inventory_does_not_modify_fixture_tree() -> None:
    before = _snapshot_tree(FIXTURE_ROOT)
    result = _run("inventory", "--root", str(FIXTURE_ROOT))
    assert result.returncode == 0, result.stderr
    after = _snapshot_tree(FIXTURE_ROOT)
    assert before == after


def test_digest_dry_run_does_not_modify_fixture_tree() -> None:
    before = _snapshot_tree(FIXTURE_ROOT)
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--dry-run",
        "--no-ollama",
    )
    assert result.returncode == 0, result.stderr
    after = _snapshot_tree(FIXTURE_ROOT)
    assert before == after
    for rel in after:
        lower = rel.lower()
        assert not any(lower.endswith(s) for s in FORBIDDEN_SUFFIXES)
        assert ".msf" not in lower or rel.endswith(".msf")
