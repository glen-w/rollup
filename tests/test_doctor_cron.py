"""Doctor and cron helper tests."""

from __future__ import annotations

import json
import plistlib
import subprocess
import sys
from pathlib import Path

from rollup.cron_helpers import (
    SchedulerPaths,
    build_scheduled_digest_argv,
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
        mail_root=FIXTURE_ROOT.parent,
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        lookback_days=7,
        folders_include=(),
        folders_exclude=(),
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
    )


def test_doctor_fast_ok(tmp_path: Path) -> None:
    pass  # mail_root is fixture parent
    report = run_doctor(_config(tmp_path), RunOptions(dry_run=True), full=False, network=False)
    assert report.schema_version == 1
    assert report.ok
    ids = {c.id for c in report.checks}
    assert "python_version" in ids
    assert "mbox_discoverable" in ids
    assert "msf_ignored" in ids


def test_doctor_json_stdout_pure(tmp_path: Path) -> None:
    pass  # mail_root is fixture parent
    report = run_doctor(_config(tmp_path), RunOptions(dry_run=True))
    text = format_doctor_json(report)
    data = json.loads(text)
    assert data["ok"] is True
    assert "checks" in data
    # Fix hints always present on check objects.
    assert all("fix" in c for c in data["checks"])


def test_doctor_cli_json(tmp_path: Path) -> None:
    pass  # mail_root is fixture parent
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
            str(FIXTURE_ROOT.parent),
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
        mail_root=FIXTURE_ROOT.parent,
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


def test_build_scheduled_digest_argv_includes_cron_flag(tmp_path: Path) -> None:
    paths = SchedulerPaths(
        python=Path(sys.executable),
        workdir=tmp_path,
        root=FIXTURE_ROOT,
        mail_root=FIXTURE_ROOT.parent,
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
    )
    argv = build_scheduled_digest_argv(paths, extra=["--no-ollama"])
    assert argv[0] == str(paths.python)
    assert argv[1:4] == ["-m", "rollup", "digest"]
    assert "--cron" in argv
    assert "--no-ollama" in argv


def test_crontab_is_shell_quoted(tmp_path: Path) -> None:
    paths = SchedulerPaths(
        python=tmp_path / "my python",
        workdir=tmp_path / "work dir",
        root=FIXTURE_ROOT,
        mail_root=FIXTURE_ROOT.parent,
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


def test_format_doctor_human_and_remote_ollama_fail(tmp_path: Path) -> None:
    from rollup.doctor import DoctorCheck, DoctorReport, format_doctor_human

    report = DoctorReport(
        schema_version=1,
        ok=False,
        error_count=1,
        warning_count=1,
        checks=(
            DoctorCheck(id="a", status="pass", message="ok path"),
            DoctorCheck(id="b", status="warn", message="soft", fix="tweak"),
            DoctorCheck(id="c", status="fail", message="hard", fix="fix it"),
        ),
    )
    text = format_doctor_human(report)
    assert "✓" in text and "⚠" in text and "✗" in text
    assert "1 error(s), 1 warning(s)" in text
    assert "fix: fix it" in text

    cfg = _config(tmp_path)
    from dataclasses import replace

    cfg = replace(
        cfg,
        no_ollama=False,
        ollama_url="http://evil.example:11434/api/generate",
        allow_remote_ollama=False,
    )
    live = run_doctor(cfg, RunOptions())
    assert live.ok is False
    loopback = next(c for c in live.checks if c.id == "ollama_loopback")
    assert loopback.status == "fail"


def test_doctor_effort_preset_check_includes_models(tmp_path: Path) -> None:
    from dataclasses import replace

    from rollup.doctor import _check_effort_preset, _check_litellm_config
    from rollup.effort import EffortModelOverride

    high = _check_effort_preset(replace(_config(tmp_path), effort="high"))
    assert high.id == "effort_preset"
    assert high.status == "info"
    assert "gpt-oss:20b" in high.message
    assert "qwen3.6:27b" in high.message

    overridden = _check_effort_preset(
        replace(
            _config(tmp_path),
            effort="high",
            effort_overrides={
                "high": EffortModelOverride(profiles={"max": "custom-max:1"})
            },
        )
    )
    assert "custom-max:1" in overridden.message

    single = _check_effort_preset(
        replace(_config(tmp_path), effort="balanced", single_model="solo:7b")
    )
    assert "solo:7b" in single.message

    assert _check_litellm_config(_config(tmp_path)) is None


def test_doctor_litellm_config_missing_extra(tmp_path: Path, monkeypatch) -> None:
    import builtins
    from dataclasses import replace

    from rollup.doctor import _check_litellm_config

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "litellm" or name.startswith("litellm."):
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    check = _check_litellm_config(
        replace(_config(tmp_path), no_ollama=False, llm_provider="litellm")
    )
    assert check is not None
    assert check.id == "litellm_extra"
    assert check.status == "fail"


def test_doctor_ollama_models_warns_when_tags_missing(
    tmp_path: Path, monkeypatch
) -> None:
    from dataclasses import replace
    from unittest.mock import MagicMock

    from rollup.doctor import _check_ollama_network

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"models": [{"name": "llama3.2:3b"}]}
    mock_resp.raise_for_status = MagicMock()
    monkeypatch.setattr("requests.get", lambda *a, **k: mock_resp)
    checks = {
        c.id: c
        for c in _check_ollama_network(
            replace(_config(tmp_path), no_ollama=False, effort="high")
        )
    }
    assert checks["ollama_reachable"].status == "pass"
    assert checks["ollama_models"].status == "warn"
    assert "gpt-oss:20b" in checks["ollama_models"].message
