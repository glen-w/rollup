"""Transactional rollup run indexing and manifest backfill."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from rollup.links_sanitize import (
    LinkSanitizeError,
    build_links_json,
    sanitize_http_url,
    validate_links_json_for_index,
)
from rollup.models import DigestEntry, DigestGroup, DigestItem, DigestReport
from rollup.payload_limits import (
    ENTRY_INDEX_VERSION,
    MAX_DATE_RAW_LEN,
    MAX_FOLDER_NAME_LEN,
    MAX_RELPATH_LEN,
    MAX_SENDER_LEN,
    MAX_SUBJECT_LEN,
    MAX_SUMMARY_LEN,
    REPORT_SCHEMA_VERSION,
    clip_text,
)
from rollup.reader_bodies import (
    ReaderBodyError,
    ReaderBodyWrite,
    build_reader_writes_for_report,
)
from rollup.reader_body_store import upsert_reader_bodies, upsert_reader_bodies_v2
from rollup.state import get_schema_version, init_db
from rollup.utc import format_utc, now_utc

logger = logging.getLogger(__name__)


class RunIndexError(ValueError):
    """Indexing failed; transaction rolled back."""


@dataclass
class IndexEntry:
    message_key: str
    source_key_observed: str | None
    group_id: str | None
    group_type: str | None
    group_display_name: str | None
    section_key: str | None
    section_position: int
    group_position: int | None
    entry_position: int
    display_position: int
    folder_name: str | None
    subject: str | None
    sender: str | None
    date_parsed: str | None
    date_raw: str | None
    newsletter_type: str | None
    summary: str | None
    summary_source: str | None
    primary_link: str | None
    links_json: str


@dataclass
class RunIndexPayload:
    run_id: str
    started_at: str
    completed_at: str | None
    status: str  # success | partial
    mode: str | None
    rollup_version: str | None
    manifest_schema_version: int | None
    report_schema_version: int | None
    stats_completeness: str  # full | manifest_partial
    window_start: str | None
    window_end: str | None
    lookback_days: int | None
    digest_fingerprint: str | None
    messages_included: int | None
    messages_skipped_outside_window: int | None
    messages_skipped_seen_undated: int | None
    messages_deduped: int | None
    messages_skipped_disabled_source: int | None
    groups_created: int | None
    sources_included: int | None
    summaries_ollama: int | None
    summaries_cache: int | None
    summaries_fallback: int | None
    summaries_errors: int | None
    summaries_final_review_applied: int | None
    group_summaries_succeeded: int | None
    warning_count: int | None
    degraded: bool
    manifest_relpath: str | None
    markdown_relpath: str | None
    html_relpath: str | None
    index_source: str  # pipeline | manifest_backfill
    entries: list[IndexEntry] = field(default_factory=list)
    reader_bodies: list[ReaderBodyWrite] = field(default_factory=list)
    expected_entry_count: int | None = None
    indexed_at: str | None = None


def _prefer_source_key(a: str, b: str) -> str:
    """Deterministic winner on conflict: list: before from:, then lexicographic."""
    def rank(k: str) -> tuple[int, str]:
        prefix = 0 if k.startswith("list:") else 1 if k.startswith("from:") else 2
        return (prefix, k)

    return a if rank(a) <= rank(b) else b


def _relative_path(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    p = Path(path)
    try:
        # Always resolve so relative paths like "output/foo.md" with root
        # "./output" become "foo.md" (not left as "output/foo.md").
        rel = p.resolve().relative_to(Path(root).resolve())
    except ValueError as exc:
        raise RunIndexError(f"artifact path escapes root: {path}") from exc
    text = str(rel)
    if text.startswith("/") or text.startswith("..") or ".." in Path(text).parts:
        raise RunIndexError(f"unsafe relative path: {text}")
    if len(text) > MAX_RELPATH_LEN:
        raise RunIndexError("relative path too long")
    return text


def _entry_from_digest_entry(
    entry: DigestEntry,
    *,
    section_key: str,
    section_position: int,
    group_id: str | None,
    group_type: str | None,
    group_display_name: str | None,
    group_position: int | None,
    entry_position: int,
    display_position: int,
    max_display_links: int = 8,
) -> IndexEntry:
    from rollup.links import prepare_links_for_render

    parsed = entry.classified.parsed
    bundle = prepare_links_for_render(
        list(parsed.link_items),
        max_main=max_display_links,
        max_other=0,
    )
    link_pairs = [(link.href, link.label or link.text) for link in bundle.main_links]
    links_json = build_links_json(link_pairs)
    validate_links_json_for_index(links_json)
    primary = sanitize_http_url(bundle.main_links[0].href) if bundle.main_links else None
    date_parsed = None
    if parsed.date_parsed is not None:
        date_parsed = format_utc(parsed.date_parsed)
    return IndexEntry(
        message_key=parsed.message_key,
        source_key_observed=parsed.source_key,
        group_id=group_id,
        group_type=group_type,
        group_display_name=group_display_name,
        section_key=section_key,
        section_position=section_position,
        group_position=group_position,
        entry_position=entry_position,
        display_position=display_position,
        folder_name=clip_text(parsed.folder_name, MAX_FOLDER_NAME_LEN),
        subject=clip_text(parsed.subject, MAX_SUBJECT_LEN),
        sender=clip_text(parsed.sender, MAX_SENDER_LEN),
        date_parsed=date_parsed,
        date_raw=clip_text(parsed.date_raw, MAX_DATE_RAW_LEN),
        newsletter_type=entry.classified.newsletter_type,
        summary=clip_text(entry.summary, MAX_SUMMARY_LEN),
        summary_source=entry.summary_source,
        primary_link=primary,
        links_json=links_json,
    )


def flatten_report_entries(
    report: DigestReport, *, max_display_links: int = 8
) -> tuple[list[IndexEntry], int]:
    """Build ordered IndexEntry list; returns (entries, duplicate_collision_count)."""
    by_key: dict[str, IndexEntry] = {}
    collisions = 0
    display_position = 0
    section_position = 0

    sections: list[tuple[str, Sequence[DigestItem]]] = list(
        report.dated_by_folder.items()
    )
    if report.undated:
        sections.append(("undated", report.undated))

    for section_key, items in sections:
        group_position = 0
        for item in items:
            if isinstance(item, DigestGroup):
                for entry_position, entry in enumerate(item.entries):
                    ie = _entry_from_digest_entry(
                        entry,
                        section_key=section_key,
                        section_position=section_position,
                        group_id=item.group_id,
                        group_type=item.group_type,
                        group_display_name=item.display_name,
                        group_position=group_position,
                        entry_position=entry_position,
                        display_position=display_position,
                        max_display_links=max_display_links,
                    )
                    if ie.message_key in by_key:
                        collisions += 1
                        continue
                    by_key[ie.message_key] = ie
                    display_position += 1
                group_position += 1
            else:
                ie = _entry_from_digest_entry(
                    item,
                    section_key=section_key,
                    section_position=section_position,
                    group_id=None,
                    group_type=None,
                    group_display_name=None,
                    group_position=None,
                    entry_position=0,
                    display_position=display_position,
                    max_display_links=max_display_links,
                )
                if ie.message_key in by_key:
                    collisions += 1
                    continue
                by_key[ie.message_key] = ie
                display_position += 1
        section_position += 1

    result = sorted(by_key.values(), key=lambda e: e.display_position)
    # Re-number display_position contiguously after dedupe.
    renumbered: list[IndexEntry] = []
    for i, e in enumerate(result):
        renumbered.append(
            IndexEntry(
                **{**e.__dict__, "display_position": i},
            )
        )
    return renumbered, collisions


def _count_leaf_entries(items: Sequence[DigestItem]) -> int:
    n = 0
    for item in items:
        if isinstance(item, DigestGroup):
            n += len(item.entries)
        else:
            n += 1
    return n


def _count_leaf_entries_report(report: DigestReport) -> int:
    n = 0
    for items in report.dated_by_folder.values():
        n += _count_leaf_entries(items)
    n += _count_leaf_entries(report.undated)
    return n


def build_pipeline_payload(
    *,
    run_id: str,
    report: DigestReport,
    status: str,
    mode: str | None,
    rollup_version: str,
    started_at: datetime,
    completed_at: datetime | None,
    md_path: Path | None,
    html_path: Path | None,
    manifest_path: Path | None,
    output_dir: Path,
    state_dir: Path,
    aggregated: Any | None = None,
    max_display_links: int = 8,
) -> RunIndexPayload:
    if status not in ("success", "partial"):
        raise RunIndexError(f"cannot index status={status!r}")
    entries, collisions = flatten_report_entries(
        report, max_display_links=max_display_links
    )
    try:
        reader_bodies, _identical, _conflicts = build_reader_writes_for_report(report)
    except ReaderBodyError as exc:
        raise RunIndexError(str(exc)) from exc
    entry_keys = {e.message_key for e in entries}
    body_keys = {b.message_key for b in reader_bodies}
    if entry_keys != body_keys:
        raise RunIndexError("reader body keys mismatch entry keys")
    stats = report.stats
    sources = {e.source_key_observed for e in entries if e.source_key_observed}
    digest_fp = None
    try:
        from rollup.final_review import compute_digest_fingerprint

        digest_fp = compute_digest_fingerprint(report)
    except Exception:
        digest_fp = hashlib.sha256(
            "\n".join(e.message_key for e in entries).encode("utf-8")
        ).hexdigest()

    fr_applied = None
    group_ok = None
    skipped_disabled = None
    if aggregated is not None:
        fr_applied = getattr(aggregated, "apply_patches_applied", None)
        gsm = getattr(report, "group_summary_metadata", None)
        if gsm is not None:
            group_ok = getattr(gsm, "groups_succeeded", None)
        filt = getattr(aggregated, "filter", None)
        if filt is not None and getattr(filt, "counts", None) is not None:
            skipped_disabled = getattr(filt.counts, "skipped_disabled_source", None)

    degraded = bool(
        getattr(aggregated, "group_summaries_degraded", False)
        or getattr(aggregated, "publication_failed", False)
        or getattr(aggregated, "seen_state_failed", False)
        or collisions
    )

    return RunIndexPayload(
        run_id=run_id,
        started_at=format_utc(started_at),
        completed_at=format_utc(completed_at) if completed_at else None,
        status=status,
        mode=mode if mode in ("manual", "cron") else None,
        rollup_version=rollup_version,
        manifest_schema_version=2 if manifest_path else None,
        report_schema_version=REPORT_SCHEMA_VERSION,
        stats_completeness="full",
        window_start=format_utc(report.window_start),
        window_end=format_utc(report.window_end),
        lookback_days=report.lookback_days,
        digest_fingerprint=digest_fp,
        messages_included=stats.dated_included + stats.undated_needing_review,
        messages_skipped_outside_window=stats.skipped_outside_window,
        messages_skipped_seen_undated=stats.skipped_seen_undated,
        messages_deduped=stats.deduped_messages,
        messages_skipped_disabled_source=skipped_disabled,
        groups_created=(
            report.grouping_metadata.groups_created if report.grouping_metadata else 0
        ),
        sources_included=len(sources),
        summaries_ollama=stats.summaries_ollama,
        summaries_cache=stats.summaries_cache,
        summaries_fallback=stats.summaries_fallback,
        summaries_errors=stats.summaries_errors,
        summaries_final_review_applied=fr_applied,
        group_summaries_succeeded=group_ok,
        warning_count=collisions,
        degraded=degraded,
        manifest_relpath=_relative_path(manifest_path, state_dir) if manifest_path else None,
        markdown_relpath=_relative_path(md_path, output_dir),
        html_relpath=_relative_path(html_path, output_dir),
        index_source="pipeline",
        entries=entries,
        reader_bodies=reader_bodies,
        expected_entry_count=len(entries),
        indexed_at=format_utc(now_utc()),
    )


_RUN_UPSERT_SQL = """
INSERT INTO rollup_runs (
    run_id, started_at, completed_at, status, mode, rollup_version,
    manifest_schema_version, report_schema_version, entry_index_version,
    stats_completeness, window_start, window_end, lookback_days, digest_fingerprint,
    messages_included, messages_skipped_outside_window, messages_skipped_seen_undated,
    messages_deduped, messages_skipped_disabled_source, groups_created, sources_included,
    summaries_ollama, summaries_cache, summaries_fallback, summaries_errors,
    summaries_final_review_applied, group_summaries_succeeded, warning_count,
    index_warning_count, degraded, manifest_relpath, markdown_relpath, html_relpath,
    index_source, indexed_at
) VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
ON CONFLICT(run_id) DO UPDATE SET
    started_at=excluded.started_at,
    completed_at=excluded.completed_at,
    status=excluded.status,
    mode=excluded.mode,
    rollup_version=excluded.rollup_version,
    manifest_schema_version=excluded.manifest_schema_version,
    report_schema_version=excluded.report_schema_version,
    entry_index_version=excluded.entry_index_version,
    stats_completeness=excluded.stats_completeness,
    window_start=excluded.window_start,
    window_end=excluded.window_end,
    lookback_days=excluded.lookback_days,
    digest_fingerprint=excluded.digest_fingerprint,
    messages_included=excluded.messages_included,
    messages_skipped_outside_window=excluded.messages_skipped_outside_window,
    messages_skipped_seen_undated=excluded.messages_skipped_seen_undated,
    messages_deduped=excluded.messages_deduped,
    messages_skipped_disabled_source=excluded.messages_skipped_disabled_source,
    groups_created=excluded.groups_created,
    sources_included=excluded.sources_included,
    summaries_ollama=excluded.summaries_ollama,
    summaries_cache=excluded.summaries_cache,
    summaries_fallback=excluded.summaries_fallback,
    summaries_errors=excluded.summaries_errors,
    summaries_final_review_applied=excluded.summaries_final_review_applied,
    group_summaries_succeeded=excluded.group_summaries_succeeded,
    warning_count=excluded.warning_count,
    index_warning_count=excluded.index_warning_count,
    degraded=excluded.degraded,
    manifest_relpath=excluded.manifest_relpath,
    markdown_relpath=excluded.markdown_relpath,
    html_relpath=excluded.html_relpath,
    index_source=excluded.index_source,
    indexed_at=excluded.indexed_at
"""


def _assert_safe_relpath(rel: str | None, *, field: str) -> None:
    if rel is None:
        return
    text = str(rel)
    if not text or text.startswith("/") or text.startswith("\\"):
        raise RunIndexError(f"unsafe {field}: absolute path rejected")
    parts = Path(text).parts
    if ".." in parts:
        raise RunIndexError(f"unsafe {field}: path traversal rejected")
    if len(text) > MAX_RELPATH_LEN:
        raise RunIndexError(f"unsafe {field}: too long")


def index_rollup_run(db_path: Path, payload: RunIndexPayload) -> None:
    """Index a run in one dedicated connection/transaction. Never uses REPLACE."""
    if payload.status not in ("success", "partial"):
        raise RunIndexError(f"cannot index status={payload.status!r}")
    if payload.stats_completeness == "full" and payload.entries is None:
        raise RunIndexError("full index requires entries")

    _assert_safe_relpath(payload.markdown_relpath, field="markdown_relpath")
    _assert_safe_relpath(payload.html_relpath, field="html_relpath")
    _assert_safe_relpath(payload.manifest_relpath, field="manifest_relpath")

    indexed_at = payload.indexed_at or format_utc(now_utc())
    entry_index_version = (
        ENTRY_INDEX_VERSION if payload.entries else 0
    )
    index_warning_count = 0

    # Deduplicate entries deterministically if caller passed duplicates.
    seen: dict[str, IndexEntry] = {}
    for entry in payload.entries:
        if entry.message_key in seen:
            index_warning_count += 1
            continue
        try:
            validate_links_json_for_index(entry.links_json)
        except LinkSanitizeError as exc:
            raise RunIndexError(str(exc)) from exc
        if entry.display_position < 0:
            raise RunIndexError("display_position must be >= 0")
        seen[entry.message_key] = entry
    entries = sorted(seen.values(), key=lambda e: e.display_position)
    # Ensure contiguous unique display_position
    positions = [e.display_position for e in entries]
    if len(positions) != len(set(positions)):
        raise RunIndexError("duplicate display_position in payload")
    if payload.expected_entry_count is not None and len(entries) != payload.expected_entry_count:
        # Allow if collisions reduced count — bump warnings
        if len(entries) < payload.expected_entry_count:
            index_warning_count += payload.expected_entry_count - len(entries)
        else:
            raise RunIndexError("entry count exceeds expected")

    conn = init_db(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            _RUN_UPSERT_SQL,
            (
                payload.run_id,
                payload.started_at,
                payload.completed_at,
                payload.status,
                payload.mode,
                payload.rollup_version,
                payload.manifest_schema_version,
                payload.report_schema_version,
                entry_index_version,
                payload.stats_completeness,
                payload.window_start,
                payload.window_end,
                payload.lookback_days,
                payload.digest_fingerprint,
                payload.messages_included,
                payload.messages_skipped_outside_window,
                payload.messages_skipped_seen_undated,
                payload.messages_deduped,
                payload.messages_skipped_disabled_source,
                payload.groups_created,
                payload.sources_included,
                payload.summaries_ollama,
                payload.summaries_cache,
                payload.summaries_fallback,
                payload.summaries_errors,
                payload.summaries_final_review_applied,
                payload.group_summaries_succeeded,
                payload.warning_count,
                index_warning_count,
                1 if payload.degraded or index_warning_count else 0,
                payload.manifest_relpath,
                payload.markdown_relpath,
                payload.html_relpath,
                payload.index_source,
                indexed_at,
            ),
        )
        conn.execute("DELETE FROM rollup_entries WHERE run_id = ?", (payload.run_id,))
        for entry in entries:
            conn.execute(
                """INSERT INTO rollup_entries (
                    run_id, message_key, source_key_observed, group_id, group_type,
                    group_display_name, section_key, section_position, group_position,
                    entry_position, display_position, folder_name, subject, sender,
                    date_parsed, date_raw, newsletter_type, summary, summary_source,
                    primary_link, links_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    payload.run_id,
                    entry.message_key,
                    entry.source_key_observed,
                    entry.group_id,
                    entry.group_type,
                    entry.group_display_name,
                    entry.section_key,
                    entry.section_position,
                    entry.group_position,
                    entry.entry_position,
                    entry.display_position,
                    entry.folder_name,
                    entry.subject,
                    entry.sender,
                    entry.date_parsed,
                    entry.date_raw,
                    entry.newsletter_type,
                    entry.summary,
                    entry.summary_source,
                    entry.primary_link,
                    entry.links_json,
                ),
            )
            if entry.source_key_observed:
                _upsert_message_source_link(
                    conn,
                    entry.message_key,
                    entry.source_key_observed,
                    indexed_at,
                )
        # Re-set entry_index_version after successful entry insert
        conn.execute(
            """UPDATE rollup_runs SET entry_index_version = ?, index_warning_count = ?
               WHERE run_id = ?""",
            (entry_index_version, index_warning_count, payload.run_id),
        )
        if payload.reader_bodies:
            ver = get_schema_version(conn)
            if ver >= 10:
                upsert_reader_bodies_v2(conn, payload.reader_bodies, seen_at=indexed_at)
            else:
                upsert_reader_bodies(conn, payload.reader_bodies, seen_at=indexed_at)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _upsert_message_source_link(
    conn,
    message_key: str,
    source_key: str,
    updated_at: str,
) -> None:
    row = conn.execute(
        "SELECT source_key_observed FROM message_source_links WHERE message_key = ?",
        (message_key,),
    ).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO message_source_links (message_key, source_key_observed, updated_at)
               VALUES (?, ?, ?)""",
            (message_key, source_key, updated_at),
        )
        return
    winner = _prefer_source_key(row[0], source_key)
    conn.execute(
        """UPDATE message_source_links
           SET source_key_observed = ?, updated_at = ?
           WHERE message_key = ?""",
        (winner, updated_at, message_key),
    )


def backfill_run_from_manifest(
    db_path: Path,
    manifest: dict[str, Any],
    *,
    state_dir: Path,
    output_dir: Path,
) -> bool:
    """Insert/update metadata-only run from manifest. Returns True if written."""
    status = manifest.get("status")
    if status not in ("success", "partial"):
        return False
    if not manifest.get("dated_outputs_written") and not manifest.get("outputs_published"):
        return False
    run_id = manifest.get("run_id")
    if not run_id:
        return False
    outputs = manifest.get("outputs") or {}
    md_rel = outputs.get("markdown")
    html_rel = outputs.get("html")
    for rel in (md_rel, html_rel):
        if rel is not None and (str(rel).startswith("/") or ".." in Path(str(rel)).parts):
            raise RunIndexError(f"unsafe manifest output path: {rel}")
    counts = manifest.get("counts") or {}
    window = manifest.get("window") or {}
    summary_counts = manifest.get("summary_source_counts") or {}

    def _count(key: str) -> int | None:
        if key not in counts:
            return None
        try:
            return int(counts[key])
        except (TypeError, ValueError):
            return None

    # Manifest path relative to state_dir/manifests — store relative to state_dir
    started = str(manifest.get("started_at") or "")
    run_short = str(run_id).replace("-", "")[:8]
    # Do not invent absolute paths; leave manifest_relpath null unless known
    payload = RunIndexPayload(
        run_id=str(run_id),
        started_at=started,
        completed_at=manifest.get("completed_at"),
        status=status,
        mode=manifest.get("mode") if manifest.get("mode") in ("manual", "cron") else None,
        rollup_version=manifest.get("rollup_version"),
        manifest_schema_version=manifest.get("schema_version"),
        report_schema_version=None,
        stats_completeness="manifest_partial",
        window_start=window.get("start"),
        window_end=window.get("end"),
        lookback_days=window.get("lookback_days"),
        digest_fingerprint=None,
        messages_included=_count("messages_included"),
        messages_skipped_outside_window=_count("messages_skipped_outside_window"),
        messages_skipped_seen_undated=_count("messages_skipped_seen_undated"),
        messages_deduped=_count("messages_deduped"),
        messages_skipped_disabled_source=_count("messages_skipped_disabled_source"),
        groups_created=_count("groups_created"),
        sources_included=(manifest.get("source_registry") or {}).get("sources_known"),
        summaries_ollama=summary_counts.get("ollama"),
        summaries_cache=summary_counts.get("cache"),
        summaries_fallback=summary_counts.get("preview_fallback"),
        summaries_errors=summary_counts.get("errors"),
        summaries_final_review_applied=None,
        group_summaries_succeeded=None,
        warning_count=len(manifest.get("warnings") or []) if "warnings" in manifest else None,
        degraded=False,
        manifest_relpath=None,
        markdown_relpath=str(md_rel) if md_rel else None,
        html_relpath=str(html_rel) if html_rel else None,
        index_source="manifest_backfill",
        entries=[],
        expected_entry_count=0,
    )
    # Skip if already indexed (full entry index or prior metadata backfill).
    conn = init_db(db_path)
    try:
        row = conn.execute(
            "SELECT entry_index_version FROM rollup_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is not None:
            return False
    finally:
        conn.close()
    index_rollup_run(db_path, payload)
    return True


def reindex_from_manifests(
    db_path: Path,
    state_dir: Path,
    output_dir: Path,
) -> int:
    """Explicit backfill from manifests/. Returns number of runs written."""
    manifest_dir = Path(state_dir) / "manifests"
    if not manifest_dir.is_dir():
        return 0
    written = 0
    state_root = Path(state_dir).resolve()
    for path in sorted(manifest_dir.glob("*.json")):
        if path.name == "latest.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skipping manifest %s: %s", path, exc)
            continue
        try:
            wrote = backfill_run_from_manifest(
                db_path, data, state_dir=state_dir, output_dir=output_dir
            )
            # Store/repair manifest_relpath (resolve both sides; state_dir may be relative).
            run_id = data.get("run_id")
            if run_id:
                rel = str(path.resolve().relative_to(state_root))
                _assert_safe_relpath(rel, field="manifest_relpath")
                conn = init_db(db_path)
                try:
                    conn.execute(
                        """UPDATE rollup_runs
                           SET manifest_relpath = ?
                           WHERE run_id = ?
                             AND (manifest_relpath IS NULL OR manifest_relpath = ''
                                  OR manifest_relpath LIKE 'state/%')""",
                        (rel, run_id),
                    )
                    conn.commit()
                finally:
                    conn.close()
            if wrote:
                written += 1
        except Exception as exc:
            logger.warning("Backfill failed for %s: %s", path, exc)
    return written
