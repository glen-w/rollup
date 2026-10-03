"""Named outcomes for an empty week versus an unreadable store."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
CONFIG = FIXTURES / "empty_config.toml"


def _digest(*args: str, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "rollup",
            "--config",
            str(CONFIG),
            "digest",
            "--no-ollama",
            "--no-linkedin",
            "--no-reddit",
            "--no-webpage",
            "--no-grouping",
            "--output",
            "none",
            "--output-dir",
            str(output / "out"),
            "--state-dir",
            str(output / "state"),
            "--log-dir",
            str(output / "logs"),
            *args,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def test_empty_window_exits_0_with_named_reason(tmp_path: Path) -> None:
    root = FIXTURES / "ci" / "empty-window"
    result = _digest(
        "--lookback-days",
        "7",
        "--root",
        str(root / "Newsletters.sbd"),
        "--mail-root",
        str(root),
        output=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "empty_window:" in result.stderr
    assert "store_unreadable:" not in result.stderr


def test_unreadable_store_exits_nonzero_with_named_reason(tmp_path: Path) -> None:
    root = FIXTURES / "ci" / "corrupt"
    result = _digest(
        "--lookback-days",
        "7",
        "--root",
        str(root / "Newsletters.sbd"),
        "--mail-root",
        str(root),
        output=tmp_path,
    )
    assert result.returncode != 0
    assert "store_unreadable:" in result.stderr
    assert "empty_window:" not in result.stderr


def test_fixture_preview_includes_committed_phrase(tmp_path: Path) -> None:
    result = _digest(
        "--lookback-days",
        "4000",
        "--root",
        str(FIXTURES / "Newsletters.sbd"),
        "--mail-root",
        str(FIXTURES),
        output=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "no_ollama=True" in result.stderr
    md = next((tmp_path / "out").glob("*-newsletter-digest.md"))
    text = md.read_text(encoding="utf-8")
    assert "Quick thoughts on learning" in text
    assert len(text.encode("utf-8")) <= 262144
