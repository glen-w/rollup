"""Side-effect-free doctor checks for Admin GET (no mkdir, network, or mbox parse)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from rollup import __version__
from rollup.state import CANONICAL_TABLES, SCHEMA_VERSION, get_schema_version
from rollup.doctor import DoctorCheck, DoctorReport


def _exists_dir(check_id: str, path: Path | None, label: str) -> DoctorCheck:
    if path is None:
        return DoctorCheck(
            id=check_id,
            status="warn",
            message=f"{label} is not configured",
            fix=f"Pass --{check_id.replace('_exists', '').replace('_', '-')} when starting rollup web",
        )
    if not path.exists():
        return DoctorCheck(
            id=check_id,
            status="fail",
            message=f"{label} does not exist: {path}",
            fix=f"Create the directory or fix the configured path for {label}",
        )
    if not path.is_dir():
        return DoctorCheck(
            id=check_id,
            status="fail",
            message=f"{label} is not a directory: {path}",
            fix=f"Point {label} at a directory",
        )
    return DoctorCheck(id=check_id, status="pass", message=f"{label} exists")


def _check_sqlite_ro(conn: sqlite3.Connection) -> list[DoctorCheck]:
    out: list[DoctorCheck] = []
    try:
        ver = get_schema_version(conn)
    except Exception as exc:
        return [
            DoctorCheck(
                id="sqlite_state",
                status="fail",
                message=f"cannot read schema_version: {type(exc).__name__}",
                fix="Upgrade rollup or repair the database offline",
            )
        ]
    if ver > SCHEMA_VERSION:
        out.append(
            DoctorCheck(
                id="schema_version",
                status="fail",
                message=f"schema version {ver} is newer than this package ({SCHEMA_VERSION})",
                fix="Upgrade the rollup package",
            )
        )
    elif ver < 8:
        out.append(
            DoctorCheck(
                id="schema_version",
                status="fail",
                message=f"schema version {ver} is too old for the web UI",
                fix="Run a digest with a current rollup to migrate, then restart web",
            )
        )
    else:
        out.append(
            DoctorCheck(
                id="schema_version",
                status="pass",
                message=f"schema version {ver}",
            )
        )
    try:
        existing = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        missing = sorted(CANONICAL_TABLES - existing)
        if missing:
            out.append(
                DoctorCheck(
                    id="canonical_tables",
                    status="fail",
                    message=f"missing tables: {', '.join(missing[:8])}"
                    + ("…" if len(missing) > 8 else ""),
                    fix="Re-run startup migration by restarting rollup web after upgrade",
                )
            )
        else:
            out.append(
                DoctorCheck(
                    id="canonical_tables",
                    status="pass",
                    message=f"{len(CANONICAL_TABLES)} canonical tables present",
                )
            )
    except Exception as exc:
        out.append(
            DoctorCheck(
                id="canonical_tables",
                status="warn",
                message=f"could not list tables: {type(exc).__name__}",
            )
        )
    return out


def _check_manifest_dir(state_dir: Path) -> DoctorCheck:
    path = state_dir / "manifests"
    if not path.exists():
        return DoctorCheck(
            id="manifest_dir",
            status="info",
            message="manifests directory not present yet",
            fix="Run a digest to create manifests",
        )
    if path.is_symlink() or not path.is_dir():
        return DoctorCheck(
            id="manifest_dir",
            status="fail",
            message="manifests path is not a safe directory",
            fix="Remove the symlink or fix state_dir/manifests",
        )
    return DoctorCheck(
        id="manifest_dir",
        status="pass",
        message="manifests directory present",
    )


def run_doctor_readonly(
    conn: sqlite3.Connection,
    *,
    state_dir: Path,
    output_dir: Path | None = None,
    mail_root: Path | None = None,
    newsletter_root: Path | None = None,
    log_dir: Path | None = None,
) -> DoctorReport:
    """Compose diagnostics without mkdir, write probes, network, or mbox parse."""
    checks: list[DoctorCheck] = [
        DoctorCheck(
            id="package_version",
            status="info",
            message=f"Package rollup {__version__}",
        ),
        _exists_dir("state_exists", state_dir, "State directory"),
        _exists_dir("output_exists", output_dir, "Output directory"),
        _exists_dir("mail_root_exists", mail_root, "Mail root"),
        _exists_dir("newsletter_root_exists", newsletter_root, "Newsletter root"),
        _check_manifest_dir(state_dir),
    ]
    if log_dir is not None:
        checks.append(_exists_dir("log_exists", log_dir, "Log directory"))
    checks.extend(_check_sqlite_ro(conn))
    error_count = sum(1 for c in checks if c.status == "fail")
    warning_count = sum(1 for c in checks if c.status == "warn")
    return DoctorReport(
        schema_version=1,
        ok=error_count == 0,
        error_count=error_count,
        warning_count=warning_count,
        checks=tuple(checks),
    )


def schema_panel_snapshot(conn: sqlite3.Connection) -> dict:
    """Cheap schema panel data; never runs full foreign_key_check."""
    try:
        ver = get_schema_version(conn)
    except Exception as exc:
        return {"error": str(exc), "schema_version": None}
    existing = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    missing = sorted(CANONICAL_TABLES - existing)
    journal = "unavailable"
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
        if row:
            journal = str(row[0])
    except sqlite3.OperationalError:
        pass
    return {
        "error": None,
        "schema_version": ver,
        "package_schema_version": SCHEMA_VERSION,
        "missing_tables": missing,
        "journal_mode": journal,
    }


def foreign_key_check_bounded(
    conn: sqlite3.Connection, *, limit: int = 50
) -> dict:
    """Deep-check only: collect up to ``limit`` FK violations (incremental)."""
    try:
        cur = conn.execute("PRAGMA foreign_key_check")
        rows = cur.fetchmany(limit + 1)
    except sqlite3.OperationalError as exc:
        return {"error": str(exc), "count": 0, "sample": [], "truncated": False}
    truncated = len(rows) > limit
    sample_rows = rows[:limit]
    sample = [
        {"table": r[0], "rowid": r[1], "parent": r[2], "fkid": r[3]}
        for r in sample_rows
    ]
    return {
        "error": None,
        # Exact total unknown when truncated; report examined sample size.
        "count": len(sample_rows) + (1 if truncated else 0),
        "sample": sample,
        "truncated": truncated,
    }

