"""Mbox backfill for missing reader bodies."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from rollup.discovery import iter_mbox_files
from rollup.mbox_identity import MboxIdentity, classify_mbox_mutation, snapshot_mbox
from rollup.parse import iter_parsed_messages
from rollup.reader_bodies import ReaderBodyError, make_reader_body_write
from rollup.reader_body_store import upsert_reader_bodies_v2
from rollup.safety import is_inside


@dataclass(frozen=True)
class BackfillScope:
    retained_entries_only: bool = True
    run_id: str | None = None
    # Declared but not enforced by _target_keys — do not expose in web UI.
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
    incomplete: bool = False
    mbox_snapshots: tuple[tuple[str, MboxIdentity | None], ...] = ()


@dataclass(frozen=True)
class BackfillScanPlan:
    """Mutation-free scan result. Writes happen in a separate short transaction."""

    missing: frozenset[str]
    writes: tuple  # ReaderBodyWrite
    ambiguous_keys: frozenset[str]
    scanned: int
    parse_failed: int
    incomplete: bool
    mbox_snapshots: tuple[tuple[str, MboxIdentity | None], ...]
    candidates: int


class BackfillError(ValueError):
    pass


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


def validate_newsletter_root(*, newsletter_root: Path, mail_root: Path) -> Path:
    """Require newsletter root contained under mail_root (no Inbox/Sent scan)."""
    root = Path(newsletter_root).expanduser().resolve()
    mail = Path(mail_root).expanduser().resolve()
    if not root.is_dir():
        raise BackfillError(f"newsletter root is not a directory: {root}")
    if not is_inside(root, mail) and root != mail:
        raise BackfillError(
            "newsletter root must be contained under mail_root; "
            "refusing to scan the whole mail account"
        )
    return root


def validate_run_scope(conn: sqlite3.Connection, run_id: str | None) -> None:
    if run_id is None:
        return
    row = conn.execute(
        "SELECT 1 FROM rollup_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise BackfillError(f"run_id not found in index: {run_id}")


def scan_backfill_candidates(
    conn: sqlite3.Connection,
    *,
    newsletter_root: Path,
    scope: BackfillScope,
    max_candidates: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> BackfillScanPlan:
    """Complete mutation-free scan. Proves uniqueness before any write plan.

    Identical message_key + identical content_hash may be deduplicated.
    Differing hashes mark the key ambiguous and exclude it from writes.
    Does not stop early in a way that misses later conflicting copies.
    """
    validate_run_scope(conn, scope.run_id)
    targets = _target_keys(conn, scope)
    existing = {
        r[0]
        for r in conn.execute("SELECT message_key FROM message_reader_bodies").fetchall()
    }
    missing = targets - existing
    candidates = len(missing)
    if max_candidates is not None and candidates > max_candidates:
        raise BackfillError(
            f"candidate count {candidates} exceeds cap {max_candidates}; "
            "narrow the scope (e.g. a single run_id)"
        )
    if not missing:
        return BackfillScanPlan(
            missing=frozenset(),
            writes=(),
            ambiguous_keys=frozenset(),
            scanned=0,
            parse_failed=0,
            incomplete=False,
            mbox_snapshots=(),
            candidates=0,
        )

    # hash -> first body text for identical-hash dedupe; conflicting hashes → ambiguous
    seen_hash: dict[str, str] = {}
    body_by_key: dict[str, tuple[str, str]] = {}  # key -> (hash, body)
    ambiguous: set[str] = set()
    parse_failed = 0
    scanned = 0
    snapshots: list[tuple[str, MboxIdentity | None]] = []

    for folder in iter_mbox_files(newsletter_root):
        snap = snapshot_mbox(folder.mbox_path)
        snapshots.append((str(folder.mbox_path), snap))
        if progress:
            progress(folder.folder_name)
        for parsed, err in iter_parsed_messages(
            folder.mbox_path,
            folder.folder_name,
            folder.relative_path,
            max_body_chars=200_000,
            max_display_links=8,
        ):
            scanned += 1
            if err or parsed is None:
                parse_failed += 1
                continue
            key = parsed.message_key
            if key not in missing:
                continue
            if key in ambiguous:
                continue
            if key in seen_hash:
                if seen_hash[key] != parsed.content_hash:
                    ambiguous.add(key)
                    body_by_key.pop(key, None)
                # identical hash: keep first (safe dedupe)
                continue
            seen_hash[key] = parsed.content_hash
            try:
                write = make_reader_body_write(
                    key, parsed.content_hash, parsed.body_text
                )
            except ReaderBodyError:
                parse_failed += 1
                continue
            body_by_key[key] = (parsed.content_hash, write)

    # Post-scan identity check: any disappeared/replaced/grown/shrunk mbox
    # marks the scan incomplete (no executable write plan).
    incomplete = False
    for path_s, before in snapshots:
        after = snapshot_mbox(Path(path_s))
        if classify_mbox_mutation(before, after) is not None:
            incomplete = True
            break

    writes = tuple(
        body_by_key[k][1]
        for k in sorted(body_by_key)
        if k not in ambiguous
    )
    return BackfillScanPlan(
        missing=frozenset(missing),
        writes=() if incomplete else writes,
        ambiguous_keys=frozenset(ambiguous),
        scanned=scanned,
        parse_failed=parse_failed,
        incomplete=incomplete,
        mbox_snapshots=tuple(snapshots),
        candidates=candidates,
    )


def verify_mbox_snapshots(
    snapshots: tuple[tuple[str, MboxIdentity | None], ...]
) -> None:
    """Re-check mbox identities under the maintenance lock before writing."""
    for path_s, before in snapshots:
        after = snapshot_mbox(Path(path_s))
        code = classify_mbox_mutation(before, after)
        if code is not None:
            raise BackfillError(f"mbox changed since preview ({code}): {path_s}")


def apply_backfill_writes(
    conn: sqlite3.Connection,
    plan: BackfillScanPlan,
    *,
    commit: bool = True,
) -> BackfillResult:
    """Apply validated writes. Caller owns the transaction when commit=False."""
    if plan.incomplete:
        raise BackfillError("refusing to write from an incomplete backfill scan")
    verify_mbox_snapshots(plan.mbox_snapshots)
    if not plan.writes:
        return BackfillResult(
            candidates=plan.candidates,
            scanned=plan.scanned,
            matched=0,
            parse_failed=plan.parse_failed,
            ambiguous=len(plan.ambiguous_keys),
            source_missing=plan.candidates,
            incomplete=plan.incomplete,
            mbox_snapshots=plan.mbox_snapshots,
        )
    stats = upsert_reader_bodies_v2(conn, list(plan.writes))
    if commit:
        conn.commit()
    return BackfillResult(
        candidates=plan.candidates,
        scanned=plan.scanned,
        matched=len(plan.writes),
        inserted=stats.inserted,
        updated=stats.updated,
        unchanged=stats.unchanged,
        conflicts=stats.conflicts,
        empty=sum(1 for w in plan.writes if not w.body_text),
        truncated=sum(1 for w in plan.writes if w.truncated),
        parse_failed=plan.parse_failed,
        ambiguous=len(plan.ambiguous_keys),
        source_missing=plan.candidates - len(plan.writes) - len(plan.ambiguous_keys),
        incomplete=False,
        mbox_snapshots=plan.mbox_snapshots,
    )


def run_backfill(
    conn: sqlite3.Connection,
    *,
    mail_root: Path,
    scope: BackfillScope,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
    newsletter_root: Path | None = None,
    max_candidates: int | None = None,
    commit: bool = True,
) -> BackfillResult:
    """Scan then optionally write. Prefer newsletter_root; mail_root kept for CLI.

    Always validates newsletter containment under mail_root. When newsletter_root
    is omitted, mail_root is used as the scan root (CLI compatibility) and must
    still pass validate_newsletter_root (equal paths are allowed).
    """
    mail = Path(mail_root)
    news = Path(newsletter_root) if newsletter_root is not None else mail
    root = validate_newsletter_root(newsletter_root=news, mail_root=mail)
    plan = scan_backfill_candidates(
        conn,
        newsletter_root=root,
        scope=scope,
        max_candidates=max_candidates,
        progress=progress,
    )
    if dry_run:
        return BackfillResult(
            candidates=plan.candidates,
            scanned=plan.scanned,
            matched=len(plan.writes),
            parse_failed=plan.parse_failed,
            ambiguous=len(plan.ambiguous_keys),
            source_missing=plan.candidates - len(plan.writes) - len(plan.ambiguous_keys),
            incomplete=plan.incomplete,
            mbox_snapshots=plan.mbox_snapshots,
        )
    return apply_backfill_writes(conn, plan, commit=commit)


def prune_orphans(
    conn: sqlite3.Connection, *, dry_run: bool = False, commit: bool = True
) -> int:
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
    if commit:
        conn.commit()
    return int(count)


def delete_all_bodies(
    conn: sqlite3.Connection, *, dry_run: bool = False, commit: bool = True
) -> int:
    count = conn.execute("SELECT COUNT(*) FROM message_reader_bodies").fetchone()[0]
    if dry_run or not count:
        return int(count)
    conn.execute("DELETE FROM message_reader_bodies")
    if commit:
        conn.commit()
    return int(count)
