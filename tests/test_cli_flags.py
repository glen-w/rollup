"""CLI flag and entry-point tests."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "Newsletters.sbd"
PROJECT_ROOT = Path(__file__).parent.parent


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "rollup", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def test_module_help() -> None:
    result = _run("--help")
    assert result.returncode == 0
    assert "inventory" in result.stdout
    assert "digest" in result.stdout


def test_console_script_help() -> None:
    rollup = shutil.which("rollup")
    if rollup is None:
        pytest.skip("rollup console script not on PATH")
    result = subprocess.run(
        [rollup, "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "inventory" in result.stdout


def test_digest_help_lists_ollama_flags() -> None:
    result = _run("digest", "--help")
    assert result.returncode == 0
    assert "--no-ollama" in result.stdout
    assert "--ollama" in result.stdout
    assert "--dry-run" in result.stdout
    assert "--summary-profile" in result.stdout
    assert "--summary-variants" in result.stdout


def test_digest_default_no_ollama(tmp_path: Path) -> None:
    """Default digest skips Ollama (no_ollama=True in log line)."""
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--dry-run",
        "--output-dir",
        str(tmp_path / "output"),
        "--state-dir",
        str(tmp_path / "state"),
        "--mail-root",
        str(tmp_path / "mail"),
    )
    assert result.returncode == 0, result.stderr
    assert "no_ollama=True" in result.stderr


def test_digest_no_ollama_flag(tmp_path: Path) -> None:
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--no-ollama",
        "--dry-run",
        "--output-dir",
        str(tmp_path / "output"),
        "--state-dir",
        str(tmp_path / "state"),
        "--mail-root",
        str(tmp_path / "mail"),
    )
    assert result.returncode == 0, result.stderr
    assert "no_ollama=True" in result.stderr


def test_digest_ollama_flag(tmp_path: Path) -> None:
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--ollama",
        "--dry-run",
        "--output-dir",
        str(tmp_path / "output"),
        "--state-dir",
        str(tmp_path / "state"),
        "--mail-root",
        str(tmp_path / "mail"),
    )
    assert result.returncode == 0, result.stderr
    assert "no_ollama=False" in result.stderr
