"""Packaging and optional-dependency isolation tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def test_no_requests_import_on_mvp_digest_path() -> None:
    code = """
import sys
mods = set(sys.modules)
import rollup.cli
from rollup.cli import build_parser, cmd_digest
parser = build_parser()
args = parser.parse_args([
    "digest", "--root", "tests/fixtures/Newsletters.sbd",
    "--dry-run", "--no-ollama",
    "--output-dir", "/tmp/rollup-test-out",
    "--state-dir", "/tmp/rollup-test-state",
    "--mail-root", "/tmp/rollup-test-mail",
])
rc = cmd_digest(args)
assert rc == 0
new = set(sys.modules) - mods
assert "requests" not in new
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_package_has_main_and_entry() -> None:
    assert (PROJECT_ROOT / "src" / "rollup" / "__main__.py").is_file()
    assert (PROJECT_ROOT / "src" / "rollup" / "__init__.py").is_file()
    assert (PROJECT_ROOT / "src" / "rollup" / "assets" / "rollup_logo.png").is_file()
    assert (PROJECT_ROOT / "src" / "rollup" / "assets" / "favicon.ico").is_file()
    text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'rollup = "rollup.cli:main"' in text


def test_gitignore_excludes_local_paths() -> None:
    gi = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    for line in ("fixtures/", "output/", "state/", "logs/", ".venv/"):
        assert line in gi
