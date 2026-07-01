"""Integration tests for full digest pipeline."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "Newsletters.sbd"
PROJECT_ROOT = Path(__file__).parent.parent


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "rollup", *args],
        cwd=cwd or PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def test_inventory_fixture(tmp_path: Path) -> None:
    result = _run("inventory", "--root", str(FIXTURE_ROOT))
    assert result.returncode == 0
    assert "brainfood" in result.stdout
    assert "tech" in result.stdout


def test_digest_no_ollama_fixture(tmp_path: Path) -> None:
    output = tmp_path / "output"
    state = tmp_path / "state"
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--no-ollama",
        "--output-dir",
        str(output),
        "--state-dir",
        str(state),
        "--mail-root",
        str(tmp_path / "mail"),
    )
    assert result.returncode == 0, result.stderr
    md_files = list(output.glob("*-newsletter-digest.md"))
    html_files = list(output.glob("*-newsletter-digest.html"))
    assert len(md_files) == 1
    assert len(html_files) == 1
    assert "Undated" in md_files[0].read_text(encoding="utf-8") or "undated" in md_files[0].read_text(encoding="utf-8").lower()


def test_digest_dry_run_no_writes(tmp_path: Path) -> None:
    output = tmp_path / "output"
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--dry-run",
        "--output-dir",
        str(output),
        "--state-dir",
        str(state),
        "--log-dir",
        str(logs),
        "--mail-root",
        str(tmp_path / "mail"),
        "--verbose",
    )
    assert result.returncode == 0, result.stderr
    assert not output.exists()
    assert not state.exists()
    assert not logs.exists()
    assert "Dry run" in result.stderr


def test_inventory_json_out_rejected_in_mail_root(tmp_path: Path) -> None:
    mail = tmp_path / "gmail"
    mail.mkdir()
    result = _run(
        "inventory",
        "--root",
        str(FIXTURE_ROOT),
        "--mail-root",
        str(mail),
        "--json-out",
        str(mail / "inventory.json"),
        "--output-dir",
        str(tmp_path / "output"),
        "--state-dir",
        str(tmp_path / "state"),
    )
    assert result.returncode != 0
    assert "mail root" in result.stderr.lower() or "ERROR" in result.stderr


def test_inventory_stdout_table_columns() -> None:
    result = _run("inventory", "--root", str(FIXTURE_ROOT))
    assert result.returncode == 0
    assert "msgs=" in result.stdout
    assert "KB" in result.stdout


def test_inventory_json_out(tmp_path: Path) -> None:
    json_path = tmp_path / "inventory.json"
    result = _run(
        "inventory",
        "--root",
        str(FIXTURE_ROOT),
        "--json-out",
        str(json_path),
        "--mail-root",
        str(tmp_path / "mail"),
        "--output-dir",
        str(tmp_path / "output"),
        "--state-dir",
        str(tmp_path / "state"),
    )
    assert result.returncode == 0, result.stderr
    assert json_path.exists()
    assert "folder_name" in json_path.read_text(encoding="utf-8")


def test_digest_exclude_folder(tmp_path: Path) -> None:
    output = tmp_path / "output"
    state = tmp_path / "state"
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--no-ollama",
        "--exclude-folder",
        "hoops",
        "--output-dir",
        str(output),
        "--state-dir",
        str(state),
        "--mail-root",
        str(tmp_path / "mail"),
    )
    assert result.returncode == 0
    md = list(output.glob("*-newsletter-digest.md"))[0].read_text(encoding="utf-8").lower()
    assert "hoops" not in md


def test_digest_stats_in_stdout(tmp_path: Path) -> None:
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
    assert result.returncode == 0
    for field in (
        "Folders scanned:",
        "Messages parsed:",
        "Dated included:",
        "Undated needing review:",
        "Skipped outside window:",
        "Skipped seen undated:",
        "Parse errors:",
        "Summaries:",
    ):
        assert field in result.stdout


def test_safety_rejects_state_in_mail_root(tmp_path: Path) -> None:
    mail = tmp_path / "gmail"
    mail.mkdir()
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--no-ollama",
        "--mail-root",
        str(mail),
        "--output-dir",
        str(tmp_path / "output"),
        "--state-dir",
        str(mail / "state"),
    )
    assert result.returncode != 0


def test_seen_undated_skipped_on_second_run(tmp_path: Path) -> None:
    output = tmp_path / "output"
    state = tmp_path / "state"
    common = [
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--no-ollama",
        "--folder",
        "misc",
        "--output-dir",
        str(output),
        "--state-dir",
        str(state),
        "--mail-root",
        str(tmp_path / "mail"),
    ]
    first = _run(*common)
    assert first.returncode == 0, first.stderr
    assert "Undated needing review:" in first.stdout
    first_undated = int(
        next(
            line.split(":")[1].strip()
            for line in first.stdout.splitlines()
            if line.startswith("Undated needing review:")
        )
    )
    assert first_undated >= 1

    second = _run(*common)
    assert second.returncode == 0, second.stderr
    second_undated = int(
        next(
            line.split(":")[1].strip()
            for line in second.stdout.splitlines()
            if line.startswith("Undated needing review:")
        )
    )
    skipped_seen = int(
        next(
            line.split(":")[1].strip()
            for line in second.stdout.splitlines()
            if line.startswith("Skipped seen undated:")
        )
    )
    assert second_undated == 0
    assert skipped_seen >= 1

    third = _run(*common, "--include-seen-undated")
    assert third.returncode == 0
    third_undated = int(
        next(
            line.split(":")[1].strip()
            for line in third.stdout.splitlines()
            if line.startswith("Undated needing review:")
        )
    )
    assert third_undated >= 1


def test_partial_write_does_not_update_seen_state(tmp_path: Path, monkeypatch) -> None:
    from rollup import cli

    output = tmp_path / "output"
    state = tmp_path / "state"

    def boom(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(cli, "atomic_write_digest", boom)

    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "digest",
            "--root",
            str(FIXTURE_ROOT),
            "--no-ollama",
            "--folder",
            "misc",
            "--output-dir",
            str(output),
            "--state-dir",
            str(state),
            "--mail-root",
            str(tmp_path / "mail"),
        ]
    )
    assert cli.cmd_digest(args) == 1
    db = state / "rollup.db"
    if db.exists():
        import sqlite3

        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM seen_messages").fetchone()[0]
        conn.close()
        assert count == 0


def test_digest_folder_filter(tmp_path: Path) -> None:
    output = tmp_path / "output"
    state = tmp_path / "state"
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--no-ollama",
        "--folder",
        "tech",
        "--output-dir",
        str(output),
        "--state-dir",
        str(state),
        "--mail-root",
        str(tmp_path / "mail"),
    )
    assert result.returncode == 0
    md = list(output.glob("*-newsletter-digest.md"))[0].read_text(encoding="utf-8")
    assert "tech" in md.lower()
    assert "brainfood" not in md.lower()


def test_safety_rejects_output_in_mail_root(tmp_path: Path) -> None:
    mail = tmp_path / "gmail"
    mail.mkdir()
    newsletters = mail / "Newsletters.sbd"
    newsletters.mkdir()
    result = _run(
        "digest",
        "--root",
        str(FIXTURE_ROOT),
        "--no-ollama",
        "--mail-root",
        str(mail),
        "--output-dir",
        str(mail / "output"),
        "--state-dir",
        str(tmp_path / "state"),
    )
    assert result.returncode != 0
    assert "mail root" in result.stderr.lower() or "ERROR" in result.stderr
