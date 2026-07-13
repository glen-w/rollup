"""Mbox backfill for missing reader bodies."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from rollup.discovery import iter_mbox_files
from rollup.parse import iter_parsed_messages
from rollup.reader_bodies import ReaderBodyError, make_reader_body_write
from rollup.reader_body_store import upsert_reader_bodies_v2


@dataclass(frozen=True)
class BackfillScope:
    retained_entries_only: bool = True
    run_id: str | None = None
    source_key: str | None = None
    date_start: str | None = None
    date_end: str | None = None
    include_undated: bool = False


@dataclass(frozen=True)
class BackfillResult:
    candidates: int = 0
    scanned: int = 0
    matched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    conflicts: int = 0
    empty: int = 0
    truncated: int = 0
    source_missing: int = 0
    parse_failed: int = 0
    ambiguous: int = 0


def _target_keys(conn: sqlite3.Connection, scope: BackfillScope) -> set[str]:
    if scope.run_id:
        rows = conn.execute(
            "SELECT message_key FROM rollup_entries WHERE run_id = ?",
            (scope.run_id,),
        ).fetchall()
        return {r[0] for r in rows}
    if scope.retained_entries_only:
        rows = conn.execute("SELECT DISTINCT message_key FROM rollup_entries").fetchall()
        return {r[0] for r in rows}
    rows = conn.execute("SELECT message_key FROM message_reader_bodies").fetchall()
    return {r[0] for r in rows}


def run_backfill(
    conn: sqlite3.Connection,
    *,
    mail_root: Path,
    scope: BackfillScope,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> BackfillResult:
    targets = _target_keys(conn, scope)
    existing = {
        r[0]
        for r in conn.execute("SELECT message_key FROM message_reader_bodies").fetchall()
    }
    missing = targets - existing
    result = BackfillResult(candidates=len(missing))
    if not missing:
        return result
    writes: list = []
    seen: dict[str, str] = {}
    ambiguous = 0
    parse_failed = 0
    scanned = 0
    for folder in iter_mbox_files(mail_root):
        for parsed, err in iter_parsed_messages(
            folder.mbox_path,
            folder.folder_name,
            folder.relative_folder_path,
            max_body_chars=200_000,
            max_display_links=8,
        ):
            scanned += 1
            if err or parsed is None:
                parse_failed += 1
                continue
            if parsed.message_key not in missing:
                continue
            if parsed.message_key in seen:
                if seen[parsed.message_key] != parsed.content_hash:
                    ambiguous += 1
                continue
            seen[parsed.message_key] = parsed.content_hash
            try:
                writes.append(
                    make_reader_body_write(
                        parsed.message_key,
                        parsed.content_hash,
                        parsed.body_text,
                    )
                )
            except ReaderBodyError:
                parse_failed += 1
            if len(writes) >= len(missing):
                break
        if len(writes) >= len(missing):
            break
    matched = len(writes)
    if dry_run:
        return BackfillResult(
            candidates=result.candidates,
            scanned=scanned,
            matched=matched,
            parse_failed=parse_failed,
            ambiguous=ambiguous,
            source_missing=len(missing) - matched,
        )
    if writes:
        stats = upsert_reader_bodies_v2(conn, writes)
        conn.commit()
        return BackfillResult(
            candidates=result.candidates,
            scanned=scanned,
            matched=matched,
            inserted=stats.inserted,
            updated=stats.updated,
            unchanged=stats.unchanged,
            conflicts=stats.conflicts,
            empty=sum(1 for w in writes if not w.body_text),
            truncated=sum(1 for w in writes if w.truncated),
            parse_failed=parse_failed,
            ambiguous=ambiguous,
            source_missing=len(missing) - matched,
        )
    return BackfillResult(
        candidates=result.candidates,
        scanned=scanned,
        matched=0,
        parse_failed=parse_failed,
        ambiguous=ambiguous,
        source_missing=len(missing),
    )


def prune_orphans(conn: sqlite3.Connection, *, dry_run: bool = False) -> int:
    count = conn.execute(
        """SELECT COUNT(*) FROM message_reader_bodies b
           WHERE NOT EXISTS (
             SELECT 1 FROM rollup_entries e WHERE e.message_key = b.message_key
           )"""
    ).fetchone()[0]
    if dry_run or not count:
        return int(count)
    conn.execute(
        """DELETE FROM message_reader_bodies
           WHERE message_key NOT IN (SELECT DISTINCT message_key FROM rollup_entries)"""
    )
    conn.commit()
    return int(count)


def delete_all_bodies(conn: sqlite3.Connection, *, dry_run: bool = False) -> int:
    count = conn.execute("SELECT COUNT(*) FROM message_reader_bodies").fetchone()[0]
    if dry_run or not count:
        return int(count)
    conn.execute("DELETE FROM message_reader_bodies")
    conn.commit()
    return int(count)
