"""Bounded, redacted manifest scanning for Admin diagnostics."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rollup.manifest import (
    STATUS_ENUM,
    SUPPORTED_SCHEMA_VERSIONS,
    filter_allowlisted,
    validate_manifest,
    ManifestValidationError,
)
from rollup.output_archive import resolve_output_artifact
from rollup.run_contracts import FAILURE_EXIT
from rollup.safety import is_inside

# Diagnostic fields safe to surface after allowlisting / truncation.
_MAX_STR = 200

STATUS_CSS = {
    "success": "status-success",
    "partial": "status-partial",
    "failure": "status-failure",
    "dry_run": "status-dry-run",
    "unknown": "status-unknown",
}

# Per-schema field availability: True means the field may be present.
FIELD_MATRIX: dict[int, frozenset[str]] = {
    1: frozenset(
        {
            "status",
            "run_id",
            "started_at",
            "completed_at",
            "counts",
            "warnings",
            "errors",
            "dated_outputs_written",
            "latest_outputs_updated",
            "outputs",
            "config_fingerprint",
        }
    ),
    2: frozenset(
        {
            "status",
            "run_id",
            "started_at",
            "completed_at",
            "counts",
            "warnings",
            "errors",
            "parse_error_summary",
            "dated_outputs_written",
            "latest_outputs_updated",
            "seen_state_failed",
            "manifest_write_failed",
            "outputs",
            "config_fingerprint",
            "group_summaries",
            "source_registry",
        }
    ),
}


@dataclass(frozen=True)
class ManifestScanLimits:
    max_dir_entries: int = 500
    max_files: int = 50
    max_bytes: int = 512_000


@dataclass
class PanelIssue:
    code: str
    message: str


@dataclass
class RunHealthCard:
    run_id: str
    status: str
    status_css: str
    source: str  # indexed | manifest | both
    started_at: str | None
    completed_at: str | None
    degraded: bool | None
    warning_count: int | None
    messages_included: int | None
    config_fingerprint: str | None
    diagnostics: list[str] = field(default_factory=list)
    artifact_kinds: tuple[str, ...] = ()
    conflict_note: str | None = None
    recovery_labels: tuple[str, ...] = ()


@dataclass
class ManifestHealthPanel:
    cards: list[RunHealthCard]
    issues: list[PanelIssue]
    incomplete_history_note: str
    examined: int
    parsed: int
    skipped: int


def _truncate(value: Any, *, limit: int = _MAX_STR) -> str:
    text = str(value)
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_relpath(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("/") or value.startswith("\\") or ".." in Path(value).parts:
        return None
    return value


def _redact_diag_list(items: Any, *, kind: str) -> list[str]:
    out: list[str] = []
    if not isinstance(items, list):
        return out
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        code = item.get("code") or item.get("kind") or kind
        if not isinstance(code, str):
            continue
        # Never include message/exception/subject/url bodies.
        count = item.get("count")
        folder = item.get("folder")
        parts = [_truncate(code, limit=64)]
        if isinstance(count, int):
            parts.append(f"n={count}")
        if isinstance(folder, str) and folder and "/" not in folder and "\\" not in folder:
            parts.append(f"folder={_truncate(folder, limit=40)}")
        out.append(" ".join(parts))
    return out


def _field_or_not_recorded(payload: dict[str, Any], version: int, key: str) -> Any:
    available = FIELD_MATRIX.get(version, frozenset())
    if key not in available:
        return None  # caller renders "not recorded"
    return payload.get(key, None)


def _load_indexed_runs(conn: sqlite3.Connection, *, limit: int) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """SELECT run_id, started_at, completed_at, status, degraded, warning_count,
                  messages_included, markdown_relpath, html_relpath, manifest_relpath
           FROM rollup_runs
           ORDER BY started_at DESC, run_id DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out[r[0]] = {
            "run_id": r[0],
            "started_at": r[1],
            "completed_at": r[2],
            "status": r[3],
            "degraded": bool(r[4]),
            "warning_count": r[5],
            "messages_included": r[6],
            "markdown_relpath": r[7],
            "html_relpath": r[8],
            "manifest_relpath": r[9],
        }
    return out


def _iter_manifest_candidates(
    manifest_dir: Path, limits: ManifestScanLimits
) -> tuple[list[Path], list[PanelIssue], int]:
    """Examine at most ``max_dir_entries`` directory entries (no full listing)."""
    issues: list[PanelIssue] = []
    if manifest_dir.is_symlink() or not manifest_dir.is_dir():
        issues.append(PanelIssue("manifest_dir_unsafe", "Manifest directory is unsafe"))
        return [], issues, 0
    examined = 0
    files: list[Path] = []
    capped = False
    try:
        with os.scandir(manifest_dir) as it:
            for entry in it:
                examined += 1
                if examined > limits.max_dir_entries:
                    capped = True
                    examined = limits.max_dir_entries
                    break
                name = entry.name
                if name == "latest.json" or not name.endswith(".json"):
                    continue
                try:
                    if entry.is_symlink(follow_symlinks=False):
                        issues.append(
                            PanelIssue("manifest_symlink", f"Skipped symlink {name!r}")
                        )
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                files.append(Path(entry.path))
    except OSError:
        issues.append(PanelIssue("manifest_dir_unreadable", "Cannot list manifests"))
        return [], issues, 0
    if capped:
        issues.append(
            PanelIssue(
                "manifest_dir_capped",
                f"Stopped after examining {limits.max_dir_entries} directory entries",
            )
        )
    return files, issues, examined


def _parse_manifest_file(
    path: Path, *, manifest_dir: Path, limits: ManifestScanLimits
) -> tuple[dict[str, Any] | None, PanelIssue | None]:
    try:
        resolved = path.resolve()
        root = manifest_dir.resolve()
        if not is_inside(resolved, root):
            return None, PanelIssue("manifest_escape", "Path escaped manifest directory")
        size = resolved.stat().st_size
        if size > limits.max_bytes:
            return None, PanelIssue("manifest_oversized", f"Skipped oversized {path.name}")
        raw = resolved.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None, PanelIssue("manifest_malformed", f"Non-object {path.name}")
        # Legacy alias handled inside validate_manifest via a working copy.
        working = dict(data)
        if "dated_outputs_written" not in working and "outputs_published" in working:
            working["dated_outputs_written"] = working["outputs_published"]
        validate_manifest(working)
        normalized = filter_allowlisted(working)
        version = int(normalized["schema_version"])
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            return None, PanelIssue(
                "manifest_unsupported",
                f"Unsupported schema_version={version} in {path.name}",
            )
        run_id = normalized.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return None, PanelIssue("manifest_bad_run_id", f"Missing run_id in {path.name}")
        status = normalized.get("status")
        if status not in STATUS_ENUM:
            return None, PanelIssue("manifest_bad_status", f"Bad status in {path.name}")
        return normalized, None
    except (OSError, UnicodeError, json.JSONDecodeError, ManifestValidationError, TypeError, ValueError):
        return None, PanelIssue("manifest_malformed", f"Isolated malformed {path.name}")


def _recovery_labels(status: str, diagnostics: list[str]) -> tuple[str, ...]:
    labels: list[str] = []
    joined = " ".join(diagnostics).lower()
    if status == "failure":
        if "no" in joined and "folder" in joined:
            labels.append("no_input")
        else:
            labels.append("required_publication")
    if "mbox_" in joined:
        labels.append("mbox_mutation")
    if status == "partial":
        labels.append("optional_writer")
    # Map through FAILURE_EXIT for label stability only — do not re-derive status.
    out: list[str] = []
    for name in labels:
        if name in FAILURE_EXIT:
            out.append(name)
    return tuple(out)


def _artifact_kinds(
    *,
    indexed: dict[str, Any] | None,
    manifest: dict[str, Any] | None,
    output_dir: Path,
) -> tuple[str, ...]:
    kinds: list[str] = []
    rels: dict[str, str | None] = {"md": None, "html": None, "manifest": None}
    if indexed:
        rels["md"] = indexed.get("markdown_relpath")
        rels["html"] = indexed.get("html_relpath")
        rels["manifest"] = indexed.get("manifest_relpath")
    if manifest:
        outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
        if rels["md"] is None:
            rels["md"] = _safe_relpath(outputs.get("markdown"))
        if rels["html"] is None:
            rels["html"] = _safe_relpath(outputs.get("html"))
    for kind, rel in rels.items():
        safe = _safe_relpath(rel)
        if not safe:
            continue
        if kind == "manifest":
            # Manifests live under state; existence checked by caller paths.
            kinds.append(kind)
            continue
        if resolve_output_artifact(output_dir, safe) is not None:
            kinds.append(kind)
    return tuple(kinds)


def collect_manifest_health(
    conn: sqlite3.Connection,
    *,
    state_dir: Path,
    output_dir: Path,
    limits: ManifestScanLimits | None = None,
) -> ManifestHealthPanel:
    limits = limits or ManifestScanLimits()
    note = (
        "Failure history from manifests is incomplete: a run whose manifest write "
        "failed completely cannot be discovered here."
    )
    issues: list[PanelIssue] = []
    indexed = _load_indexed_runs(conn, limit=limits.max_files)
    manifest_dir = Path(state_dir) / "manifests"
    cards_by_id: dict[str, RunHealthCard] = {}

    # Seed from indexed runs (authoritative status when present).
    for run_id, row in indexed.items():
        status = row["status"] if row["status"] in STATUS_ENUM else "unknown"
        cards_by_id[run_id] = RunHealthCard(
            run_id=run_id,
            status=status,
            status_css=STATUS_CSS.get(status, STATUS_CSS["unknown"]),
            source="indexed",
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
            degraded=row.get("degraded"),
            warning_count=row.get("warning_count"),
            messages_included=row.get("messages_included"),
            config_fingerprint=None,
            artifact_kinds=_artifact_kinds(
                indexed=row, manifest=None, output_dir=output_dir
            ),
        )

    if not manifest_dir.exists():
        cards = sorted(
            cards_by_id.values(),
            key=lambda c: (_parse_ts(c.started_at) or datetime.min.replace(tzinfo=timezone.utc), c.run_id),
            reverse=True,
        )
        return ManifestHealthPanel(
            cards=cards[: limits.max_files],
            issues=issues,
            incomplete_history_note=note,
            examined=0,
            parsed=0,
            skipped=0,
        )

    files, dir_issues, examined = _iter_manifest_candidates(manifest_dir, limits)
    issues.extend(dir_issues)

    # Parse the bounded candidate set (≤ max_dir_entries), then sort by
    # persisted timestamp and retain max_files — not "first N in dir order".
    parsed_payloads: list[dict[str, Any]] = []
    skipped = 0
    for path in files:
        payload, issue = _parse_manifest_file(
            path, manifest_dir=manifest_dir, limits=limits
        )
        if issue:
            skipped += 1
            issues.append(issue)
            continue
        assert payload is not None
        parsed_payloads.append(payload)

    parsed_payloads.sort(
        key=lambda p: (
            _parse_ts(p.get("started_at")) or datetime.min.replace(tzinfo=timezone.utc),
            str(p.get("run_id") or ""),
        ),
        reverse=True,
    )
    parsed_payloads = parsed_payloads[: limits.max_files]

    for payload in parsed_payloads:
        run_id = str(payload["run_id"])
        version = int(payload["schema_version"])
        m_status = str(payload["status"])
        diags: list[str] = []
        diags.extend(_redact_diag_list(payload.get("warnings"), kind="warning"))
        diags.extend(_redact_diag_list(payload.get("errors"), kind="error"))
        diags.extend(
            _redact_diag_list(payload.get("parse_error_summary"), kind="parse")
        )
        fp = payload.get("config_fingerprint")
        fp_s = _truncate(fp, limit=64) if isinstance(fp, str) else None

        existing = cards_by_id.get(run_id)
        if existing is None:
            cards_by_id[run_id] = RunHealthCard(
                run_id=run_id,
                status=m_status,
                status_css=STATUS_CSS.get(m_status, STATUS_CSS["unknown"]),
                source="manifest",
                started_at=payload.get("started_at")
                if isinstance(payload.get("started_at"), str)
                else None,
                completed_at=payload.get("completed_at")
                if isinstance(payload.get("completed_at"), str)
                else None,
                degraded=None,
                warning_count=None,
                messages_included=(
                    (payload.get("counts") or {}).get("messages_included")
                    if isinstance(payload.get("counts"), dict)
                    else None
                ),
                config_fingerprint=fp_s,
                diagnostics=diags,
                artifact_kinds=_artifact_kinds(
                    indexed=None, manifest=payload, output_dir=output_dir
                ),
                recovery_labels=_recovery_labels(m_status, diags),
            )
            continue

        conflict = None
        if existing.status != m_status and existing.source == "indexed":
            conflict = (
                f"Indexed status {existing.status!r} differs from manifest "
                f"{m_status!r}; indexed status is authoritative"
            )
        # Indexed status wins; manifesto only enriches allowlisted diagnostics.
        existing.source = "both"
        existing.diagnostics = diags
        existing.config_fingerprint = fp_s or existing.config_fingerprint
        existing.conflict_note = conflict
        existing.recovery_labels = _recovery_labels(existing.status, diags)
        existing.artifact_kinds = _artifact_kinds(
            indexed=indexed.get(run_id),
            manifest=payload,
            output_dir=output_dir,
        )
        # Touch version matrix so callers can show "not recorded".
        _ = _field_or_not_recorded(payload, version, "seen_state_failed")

    cards = sorted(
        cards_by_id.values(),
        key=lambda c: (
            _parse_ts(c.started_at) or datetime.min.replace(tzinfo=timezone.utc),
            c.run_id,
        ),
        reverse=True,
    )
    return ManifestHealthPanel(
        cards=cards[: limits.max_files],
        issues=issues[:50],
        incomplete_history_note=note,
        examined=examined,
        parsed=len(parsed_payloads),
        skipped=skipped,
    )
