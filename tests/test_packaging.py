"""Packaging and dependency tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def _run_digest_subprocess(extra_args: list[str]) -> subprocess.CompletedProcess:
    code = f"""
import sys
mods = set(sys.modules)
import rollup.cli
from rollup.cli import build_parser, cmd_digest
parser = build_parser()
args = parser.parse_args([
    "digest", "--root", "tests/fixtures/Newsletters.sbd",
    *{extra_args!r},
])
rc = cmd_digest(args)
new = sorted(set(sys.modules) - mods)
print("NEW_MODULES:", ",".join(new))
raise SystemExit(rc)
"""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def test_no_requests_import_on_no_ollama_digest_path() -> None:
    """Default digest path must not load requests (lazy import only for --ollama)."""
    result = _run_digest_subprocess(
        [
            "--dry-run",
            "--no-ollama",
            "--output-dir",
            "/tmp/rollup-test-out",
            "--state-dir",
            "/tmp/rollup-test-state",
            "--mail-root",
            "/tmp/rollup-test-mail",
        ]
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "requests" not in result.stdout


def test_no_requests_import_on_default_digest_write_path(tmp_path: Path) -> None:
    out = tmp_path / "output"
    state = tmp_path / "state"
    mail = tmp_path / "mail"
    result = _run_digest_subprocess(
        [
            "--output-dir",
            str(out),
            "--state-dir",
            str(state),
            "--mail-root",
            str(mail),
        ]
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "requests" not in result.stdout
    assert list(out.glob("*-newsletter-digest.md"))


def test_inventory_does_not_import_requests() -> None:
    code = """
import sys
mods = set(sys.modules)
import rollup.cli
from rollup.cli import build_parser, cmd_inventory
parser = build_parser()
args = parser.parse_args([
    "inventory", "--root", "tests/fixtures/Newsletters.sbd",
])
rc = cmd_inventory(args)
new = set(sys.modules) - mods
assert "requests" not in new
raise SystemExit(rc)
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


def test_prompts_bundled_in_package() -> None:
    prompts_dir = PROJECT_ROOT / "src" / "rollup" / "prompts"
    assert (prompts_dir / "_common.txt").is_file()
    for name in (
        "short_update",
        "multi_section_digest",
        "essay",
        "link_roundup",
        "unclassified",
    ):
        assert (prompts_dir / f"{name}.txt").is_file()
    from rollup.summarize import PROMPTS_DIR

    assert PROMPTS_DIR == prompts_dir


def test_requests_is_core_dependency() -> None:
    text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "requests>=" in text
    assert "[project.optional-dependencies]" in text
    assert "ollama =" not in text


def test_gitignore_excludes_local_paths() -> None:
    gi = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    for line in ("fixtures/", "output/", "state/", "logs/", ".venv/"):
        assert line in gi
