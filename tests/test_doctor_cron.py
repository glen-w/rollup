"""Doctor and cron helper tests."""

from __future__ import annotations

import json
import plistlib
import subprocess
import sys
from pathlib import Path

from rollup.cron_helpers import (
    SchedulerPaths,
    format_cron_status,
    render_crontab,
    render_launchd_plist,
    resolve_python,
)
from rollup.doctor import format_doctor_json, run_doctor
from rollup.run_options import RunOptions

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "Newsletters.sbd"
PROJECT_ROOT = Path(__file__).parent.parent


def _config(tmp_path: Path):
    from rollup.config import Config

    return Config(
        root=FIXTURE_ROOT,
        mail_root=tmp_path / "mail",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        lookback_days=7,
        folders_include=(),
        folders_exclude=(),
        dry_run=True,
        no_ollama=True,
        include_seen_undated=False,
        rebuild_summaries=False,
        max_body_chars=200_000,
        max_chars_for_llm=30_000,
        max_display_links=8,
        ollama_url="http://localhost:11434/api/generate",
        ollama_model="llama3.2:3b",
        allow_remote_ollama=False,
        summary_profile=None,
        summary_variants=(),
        summary_type_routing=None,
        summary_profile_set_path=None,
        export_summary_profile_set_path=None,
        list_summary_profiles=False,
        list_newsletter_types=False,
        summary_routing_report=False,
        verbose=False,
        quiet=False,
    )


def test_doctor_fast_ok(tmp_path: Path) -> None:
    (tmp_path / "mail").mkdir()
    report = run_doctor(_config(tmp_path), RunOptions(dry_run=True), full=False, network=False)
    assert report.schema_version == 1
    assert report.ok
    ids = {c.id for c in report.checks}
    assert "python_version" in ids
    assert "mbox_discoverable" in ids
    assert "msf_ignored" in ids


def test_doctor_json_stdout_pure(tmp_path: Path) -> None:
    (tmp_path / "mail").mkdir()
    report = run_doctor(_config(tmp_path), RunOptions(dry_run=True))
    text = format_doctor_json(report)
    data = json.loads(text)
    assert data["ok"] is True
    assert "checks" in data
    # Fix hints always present on check objects.
    assert all("fix" in c for c in data["checks"])


def test_doctor_cli_json(tmp_path: Path) -> None:
    (tmp_path / "mail").mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rollup",
            "doctor",
            "--json",
            "--root",
            str(FIXTURE_ROOT),
            "--mail-root",
            str(tmp_path / "mail"),
            "--output-dir",
            str(tmp_path / "output"),
            "--state-dir",
            str(tmp_path / "state"),
            "--log-dir",
            str(tmp_path / "logs"),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    data = json.loads(result.stdout)
    assert data["ok"] is True
    # stdout is JSON only
    assert result.stdout.lstrip().startswith("{")


def test_launchd_plist_validates(tmp_path: Path) -> None:
    paths = SchedulerPaths(
        python=Path(sys.executable),
        workdir=tmp_path,
        root=FIXTURE_ROOT,
        mail_root=tmp_path / "mail",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
    )
    raw = render_launchd_plist(paths)
    plist = plistlib.loads(raw)
    assert plist["Label"] == "com.rollup.digest"
    assert "WorkingDirectory" in plist
    assert "StandardOutPath" in plist
    assert "StandardErrorPath" in plist
    assert Path(plist["ProgramArguments"][0]) == Path(sys.executable)


def test_crontab_is_shell_quoted(tmp_path: Path) -> None:
    paths = SchedulerPaths(
        python=tmp_path / "my python",
        workdir=tmp_path / "work dir",
        root=FIXTURE_ROOT,
        mail_root=tmp_path / "mail",
        output_dir=tmp_path / "out dir",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
    )
    text = render_crontab(paths)
    assert "Weekly non-AI digest" in text
    assert "my python" in text
    assert "work dir" in text
    assert "out dir" in text


def test_cron_status_empty(tmp_path: Path) -> None:
    msg = format_cron_status(tmp_path)
    assert "No previous" in msg


def test_resolve_python_warns_without_explicit() -> None:
    path, warnings = resolve_python(None)
    assert path.exists()
    assert warnings
