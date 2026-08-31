"""Derive Run Studio progress from digest subprocess log lines."""

from __future__ import annotations

import re
from typing import Any

_DIGEST_RE = re.compile(r"Digest: root=.+ folders=(\d+)")
_PARSING_RE = re.compile(r"Parsing (\S+)")
_LLM_RE = re.compile(r"(?:LLM|Ollama) \[(\d+)/(\d+)\] summarising: (.+)")
_WRITER_RE = re.compile(r"Running output writer (\S+)")
_STATS_RE = re.compile(r"Folders scanned: (\d+)")

_STATUS_LABELS = {
    "success": "Complete",
    "partial": "Complete (partial)",
    "failure": "Failed",
    "dry_run": "Dry-run complete",
}


def parse_run_progress(
    log_lines: list[str],
    *,
    dry_run: bool,
    status: str,
) -> dict[str, Any]:
    """Return phase, percent, and detail for the Run Studio progress UI."""
    if status != "running":
        return {
            "phase": "complete",
            "phase_label": _STATUS_LABELS.get(status, "Complete"),
            "percent": 100,
            "detail": None,
            "llm_current": None,
            "llm_total": None,
        }

    folders_total: int | None = None
    parsed_count = 0
    llm_current: int | None = None
    llm_total: int | None = None
    writers_started: list[str] = []
    last_detail: str | None = None
    saw_digest = False
    saw_stats = False

    for line in log_lines:
        match = _DIGEST_RE.search(line)
        if match:
            folders_total = int(match.group(1))
            saw_digest = True
        match = _PARSING_RE.search(line)
        if match:
            parsed_count += 1
            last_detail = f"Parsing {match.group(1)}"
        match = _LLM_RE.search(line)
        if match:
            llm_current = int(match.group(1))
            llm_total = int(match.group(2))
            subject = match.group(3).strip().strip("'\"")
            if len(subject) > 72:
                subject = subject[:69] + "…"
            last_detail = subject
        match = _WRITER_RE.search(line)
        if match:
            writers_started.append(match.group(1))
            last_detail = f"Writing {match.group(1)}"
        if _STATS_RE.search(line):
            saw_stats = True
        if "Archived" in line and "prior digest" in line:
            last_detail = "Archiving prior outputs"

    if saw_stats:
        phase = "finishing"
        label = "Finishing up"
        percent = 95
    elif writers_started and not dry_run:
        phase = "writing"
        label = "Writing outputs"
        percent = min(92, 82 + len(writers_started) * 4)
    elif llm_total:
        phase = "summarizing"
        label = "Summarising with LLM"
        percent = 40 + int(42 * llm_current / llm_total)
    elif parsed_count > 0 or saw_digest:
        phase = "parsing"
        label = "Parsing mailboxes" if not dry_run else "Dry-run discovery"
        if folders_total and folders_total > 0:
            percent = 10 + int(28 * min(parsed_count, folders_total) / folders_total)
        else:
            percent = 10 + min(parsed_count * 6, 28)
    elif saw_digest:
        phase = "discovering"
        label = "Discovering folders"
        percent = 8
    else:
        phase = "starting"
        label = "Starting"
        percent = 3

    return {
        "phase": phase,
        "phase_label": label,
        "percent": min(percent, 99),
        "detail": last_detail,
        "llm_current": llm_current,
        "llm_total": llm_total,
    }
