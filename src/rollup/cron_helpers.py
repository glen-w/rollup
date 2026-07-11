"""Scheduler snippet rendering and cron status display."""

from __future__ import annotations

import plistlib
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from rollup.manifest import read_latest_manifest


@dataclass(frozen=True)
class SchedulerPaths:
    python: Path
    workdir: Path
    root: Path
    mail_root: Path
    output_dir: Path
    state_dir: Path
    log_dir: Path


def resolve_python(explicit: str | None = None) -> tuple[Path, list[str]]:
    """Return (python_path, warnings). Prefer explicit --python."""
    warnings: list[str] = []
    if explicit:
        path = Path(explicit).expanduser().resolve()
    else:
        path = Path(sys.executable).resolve()
        warnings.append(
            f"Using current interpreter {path}; pass --python for a stable absolute path "
            "(pipx/uv/Homebrew shims may change)."
        )
    lower = str(path).lower()
    for marker in ("pyenv", "asdf", "shims", "/opt/homebrew/bin/python"):
        if marker in lower:
            warnings.append(
                f"Interpreter path looks like a shim or managed install ({marker}); "
                "prefer a project venv binary for launchd."
            )
            break
    return path, warnings


def build_digest_argv(paths: SchedulerPaths, *, extra: list[str] | None = None) -> list[str]:
    args = [
        str(paths.python),
        "-m",
        "rollup",
        "digest",
        "--cron",
        "--root",
        str(paths.root),
        "--mail-root",
        str(paths.mail_root),
        "--output-dir",
        str(paths.output_dir),
        "--state-dir",
        str(paths.state_dir),
        "--log-dir",
        str(paths.log_dir),
    ]
    if extra:
        args.extend(extra)
    return args


def render_crontab(
    paths: SchedulerPaths,
    *,
    schedule: str = "0 8 * * 0",
    extra: list[str] | None = None,
) -> str:
    """Render a crontab line with shell-quoted paths (alternative to launchd)."""
    argv = build_digest_argv(paths, extra=extra)
    cmd = (
        f"cd {shlex.quote(str(paths.workdir))} && "
        + " ".join(shlex.quote(a) for a in argv)
        + f" >> {shlex.quote(str(paths.log_dir / 'cron.log'))} 2>&1"
    )
    return (
        "# Weekly non-AI digest (macOS: prefer launchd — see print-launchd)\n"
        f"{schedule} {cmd}\n"
    )


def render_launchd_plist(
    paths: SchedulerPaths,
    *,
    label: str = "com.rollup.digest",
    weekday: int = 0,
    hour: int = 8,
    minute: int = 0,
    extra: list[str] | None = None,
) -> bytes:
    """Render a launchd LaunchAgent plist (preferred on macOS)."""
    argv = build_digest_argv(paths, extra=extra)
    # ProgramArguments should not include the shell; python is Program.
    program_arguments = argv
    plist = {
        "Label": label,
        "ProgramArguments": program_arguments,
        "WorkingDirectory": str(paths.workdir),
        "StartCalendarInterval": {
            "Weekday": weekday,
            "Hour": hour,
            "Minute": minute,
        },
        "StandardOutPath": str(paths.log_dir / "launchd.out.log"),
        "StandardErrorPath": str(paths.log_dir / "launchd.err.log"),
        "RunAtLoad": False,
    }
    return plistlib.dumps(plist)


def format_cron_status(state_dir: Path) -> str:
    latest = read_latest_manifest(Path(state_dir) / "manifests")
    if latest is None:
        return "No previous successful/latest manifest found under state/manifests/."
    lines = [
        f"run_id: {latest.get('run_id')}",
        f"status: {latest.get('status')}",
        f"mode: {latest.get('mode')}",
        f"started_at: {latest.get('started_at')}",
        f"completed_at: {latest.get('completed_at')}",
        f"outputs_published: {latest.get('outputs_published')}",
        f"latest_outputs_updated: {latest.get('latest_outputs_updated')}",
    ]
    counts = latest.get("counts") or {}
    if counts:
        lines.append(
            f"messages_included: {counts.get('messages_included')} "
            f"(parsed={counts.get('messages_parsed')})"
        )
    return "\n".join(lines)
