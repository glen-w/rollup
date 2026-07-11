"""Integration coverage for cron, manifests, and latest publication."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "Newsletters.sbd"
PROJECT_ROOT = Path(__file__).parent.parent


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "rollup", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def test_cron_digest_writes_manifest_and_latest(tmp_path: Path) -> None:
    output = tmp_path / "output"
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    mail = tmp_path / "mail"
    mail.mkdir()
    result = _run(
        "digest",
        "--cron",
        "--no-ollama",
        "--no-grouping",
        "--root",
        str(FIXTURE_ROOT),
        "--mail-root",
        str(mail),
        "--output-dir",
        str(output),
        "--state-dir",
        str(state),
        "--log-dir",
        str(logs),
    )
    assert result.returncode == 0, result.stderr
    assert list(output.glob("*-newsletter-digest.md"))
    assert (output / "latest.md").exists()
    assert (output / "latest.html").exists()
    manifests = list((state / "manifests").glob("*.json"))
    assert manifests
    latest = state / "manifests" / "latest.json"
    assert latest.exists()
    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert payload["status"] == "success"
    assert payload["mode"] == "cron"
    assert payload["outputs_published"] is True
    assert payload["latest_outputs_updated"] is True
    assert "subject" not in payload
    assert "body_text" not in payload


def test_parse_edge_bad_date_is_anomaly_not_fatal() -> None:
    from email.message import EmailMessage

    from rollup.parse import _parse_date, parse_message

    dt, anomaly = _parse_date("not-a-real-date")
    assert dt is None
    assert anomaly == "date_invalid"

    msg = EmailMessage()
    msg["Subject"] = "Bad Date"
    msg["From"] = "edge@example.com"
    msg["Date"] = "Completely Invalid Date Value"
    msg["Message-ID"] = "<bad-date-unit@example.com>"
    msg.set_content("Body with an unparseable date.")
    parsed = parse_message(msg, "edge", "edge", 200_000, 8)
    assert parsed.date_parsed is None
    assert "date_invalid" in parsed.parse_warnings


def test_parse_edge_empty_body_fixture() -> None:
    from rollup.discovery import iter_mbox_files
    from rollup.parse import parse_mbox_folder

    folders = [
        f
        for f in iter_mbox_files(FIXTURE_ROOT)
        if f.folder_name == "empty_bodies" or "empty_bodies" in f.relative_path
    ]
    assert folders
    msgs, errors, folder_errors = parse_mbox_folder(folders[0], 200_000, 8)
    assert not folder_errors
    assert errors == 0
    assert msgs
    assert "empty_body" in msgs[0].parse_warnings
