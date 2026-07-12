"""Reader body integrity checks and aggregate statistics."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from rollup.payload_limits import MAX_READER_BODY_LEN
from rollup.state import SCHEMA_VERSION, get_schema_version

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class IssueCount:
    code: str
    count: int


@dataclass(frozen=True)
class ReaderBodyStats:
    total_rows: int
    populated: int
    empty: int
    truncated: int
    retained_entries: int
    entries_with_body: int
    entries_missing_body: int
    orphans: int
    coverage_pct: float | None
    coverage_numerator: int
    coverage_denominator: int
    db_file_bytes: int
    table_storage: str
    by_reader_version: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckReport:
    schema_version: int
    issues: tuple[IssueCount, ...]


def _db_file_size(db_path: Path) -> int:
    try:
        return db_path.stat().st_size
    except OSError:
        return 0


def _table_storage_estimate(conn: sqlite3.Connection) -> str:
    try:
        row = conn.execute(
            "SELECT SUM(pgsize) FROM dbstat WHERE name = 'message_reader_bodies'"
        ).fetchone()
        if row and row[0] is not None:
            return str(int(row[0]))
    except sqlite3.OperationalError:
        pass
    return "unavailable"


def collect_stats(conn: sqlite3.Connection, *, db_path: Path) -> ReaderBodyStats:
    total = int(conn.execute("SELECT COUNT(*) FROM message_reader_bodies").fetchone()[0])
    populated = int(
        conn.execute(
            "SELECT COUNT(*) FROM message_reader_bodies WHERE length(body_text) > 0"
        ).fetchone()[0]
    )
    empty = total - populated
    truncated = int(
        conn.execute(
            "SELECT COUNT(*) FROM message_reader_bodies WHERE truncated = 1"
        ).fetchone()[0]
    )
    retained = int(
        conn.execute("SELECT COUNT(DISTINCT message_key) FROM rollup_entries").fetchone()[0]
    )
    with_body = int(
        conn.execute(
            """SELECT COUNT(DISTINCT e.message_key)
               FROM rollup_entries e
               JOIN message_reader_bodies b ON b.message_key = e.message_key"""
        ).fetchone()[0]
    )
    missing = max(0, retained - with_body)
    orphans = int(
        conn.execute(
            """SELECT COUNT(*) FROM message_reader_bodies b
               WHERE NOT EXISTS (
                 SELECT 1 FROM rollup_entries e WHERE e.message_key = b.message_key
               )"""
        ).fetchone()[0]
    )
    coverage = (with_body / retained * 100.0) if retained else None
    by_ver: dict[int, int] = {}
    try:
        for row in conn.execute(
            "SELECT reader_text_version, COUNT(*) FROM message_reader_bodies GROUP BY reader_text_version"
        ):
            by_ver[int(row[0])] = int(row[1])
    except sqlite3.OperationalError:
        pass
    return ReaderBodyStats(
        total_rows=total,
        populated=populated,
        empty=empty,
        truncated=truncated,
        retained_entries=retained,
        entries_with_body=with_body,
        entries_missing_body=missing,
        orphans=orphans,
        coverage_pct=coverage,
        coverage_numerator=with_body,
        coverage_denominator=retained,
        db_file_bytes=_db_file_size(db_path),
        table_storage=_table_storage_estimate(conn),
        by_reader_version=by_ver,
    )


def run_check(conn: sqlite3.Connection) -> CheckReport:
    issues: list[IssueCount] = []
    over = conn.execute(
        "SELECT COUNT(*) FROM message_reader_bodies WHERE length(body_text) > ?",
        (MAX_READER_BODY_LEN,),
    ).fetchone()[0]
    if over:
        issues.append(IssueCount("over_cap_body", int(over)))
    bad_trunc = conn.execute(
        """SELECT COUNT(*) FROM message_reader_bodies
           WHERE truncated = 1 AND length(body_text) != ?""",
        (MAX_READER_BODY_LEN,),
    ).fetchone()[0]
    if bad_trunc:
        issues.append(IssueCount("invalid_truncation_relation", int(bad_trunc)))
    rows = conn.execute(
        "SELECT content_hash, stored_body_hash FROM message_reader_bodies"
    ).fetchall()
    bad_hash = sum(
        1 for ch, sh in rows if not _HASH_RE.match(ch or "") or not _HASH_RE.match(sh or "")
    )
    if bad_hash:
        issues.append(IssueCount("invalid_hash_format", bad_hash))
    orphans = conn.execute(
        """SELECT COUNT(*) FROM message_reader_bodies b
           WHERE NOT EXISTS (
             SELECT 1 FROM rollup_entries e WHERE e.message_key = b.message_key
           )"""
    ).fetchone()[0]
    if orphans:
        issues.append(IssueCount("orphan", int(orphans)))
    retained = conn.execute(
        "SELECT COUNT(DISTINCT message_key) FROM rollup_entries"
    ).fetchone()[0]
    with_body = conn.execute(
        """SELECT COUNT(DISTINCT e.message_key)
           FROM rollup_entries e
           JOIN message_reader_bodies b ON b.message_key = e.message_key"""
    ).fetchone()[0]
    gap = int(retained) - int(with_body)
    if gap:
        issues.append(IssueCount("coverage_gap", gap))
    return CheckReport(schema_version=get_schema_version(conn), issues=tuple(issues))


def require_schema(conn: sqlite3.Connection, *, min_version: int = 9) -> None:
    ver = get_schema_version(conn)
    if ver > SCHEMA_VERSION:
        raise RuntimeError(f"unsupported schema version {ver}")
    if ver < min_version:
        raise RuntimeError("schema migration required")
    if ver >= 9:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='message_reader_bodies'"
        ).fetchone()
        if row is None:
            raise RuntimeError("malformed reader bodies schema")
