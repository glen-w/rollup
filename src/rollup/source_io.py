"""Export/import for source overrides and aliases."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rollup.fsutil import atomic_write_text
from rollup.source_models import CADENCE_LABELS, GROUPING_POLICIES
from rollup.source_registry import (
    SourceRegistryError,
    _flatten_aliases,
    ensure_source_anchor,
    list_source_keys,
    load_alias_map,
    load_overrides,
    set_overrides,
)

EXPORT_SCHEMA_VERSION = 1
NEWSLETTER_TYPES = frozenset(
    {
        "short_update",
        "multi_section_digest",
        "essay",
        "link_roundup",
        "unclassified",
    }
)


@dataclass(frozen=True)
class ImportResult:
    created: int
    updated: int
    cleared: int
    deleted: int


def export_sources(
    conn: sqlite3.Connection,
    path: Path,
    *,
    include_observations: bool = False,
) -> None:
    aliases = load_alias_map(conn)
    override_rows = []
    anchor_keys: set[str] = set()
    for key in list_source_keys(conn, include_superseded=True):
        ov = load_overrides(conn, key)
        if all(
            getattr(ov, f) is None
            for f in (
                "enabled",
                "always_surface",
                "priority",
                "newsletter_type",
                "grouping_policy",
                "summary_profile",
                "expected_cadence",
                "display_name",
                "notes",
            )
        ):
            continue
        anchor_keys.add(key)
        override_rows.append(
            {
                "source_key": key,
                "enabled": ov.enabled,
                "always_surface": ov.always_surface,
                "priority": ov.priority,
                "newsletter_type": ov.newsletter_type,
                "grouping_policy": ov.grouping_policy,
                "summary_profile": ov.summary_profile,
                "expected_cadence": ov.expected_cadence,
                "display_name": ov.display_name,
                "notes": ov.notes,
                "updated_at": ov.updated_at,
                "updated_by": ov.updated_by,
            }
        )
    alias_rows = []
    for alias_key, canonical in sorted(aliases.items()):
        anchor_keys.add(canonical)
        alias_rows.append(
            {"alias_key": alias_key, "canonical_source_key": canonical}
        )
        anchor_keys.add(alias_key)

    anchors = []
    for key in sorted(anchor_keys):
        row = conn.execute(
            "SELECT identity_version, lifecycle, superseded_by, display_name_observed, created_at, updated_at FROM sources WHERE source_key = ?",
            (key,),
        ).fetchone()
        if not row:
            continue
        anchors.append(
            {
                "source_key": key,
                "identity_version": row[0],
                "lifecycle": row[1],
                "superseded_by": row[2],
                "display_name_observed": row[3],
                "created_at": row[4],
                "updated_at": row[5],
            }
        )

    payload: dict[str, Any] = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "exported_at": datetime.now().astimezone().isoformat(),
        "anchors": anchors,
        "overrides": sorted(override_rows, key=lambda r: r["source_key"]),
        "aliases": alias_rows,
    }
    if include_observations:
        payload["observations_note"] = "observations excluded from portable restore"
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    atomic_write_text(path, text)


def import_sources(
    conn: sqlite3.Connection,
    path: Path,
    *,
    merge: bool = True,
    dry_run: bool = False,
    replace_all: bool = False,
) -> ImportResult:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceRegistryError(f"Invalid UTF-8 in import file: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SourceRegistryError(f"Invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SourceRegistryError("Import root must be an object")
    version = int(data.get("schema_version", 0))
    if version != EXPORT_SCHEMA_VERSION:
        raise SourceRegistryError(f"Unsupported export schema_version {version}")

    anchors = data.get("anchors") or []
    overrides = data.get("overrides") or []
    aliases = data.get("aliases") or []
    if not isinstance(anchors, list) or not isinstance(overrides, list) or not isinstance(aliases, list):
        raise SourceRegistryError("anchors/overrides/aliases must be arrays")

    # Validate all rows first
    proposed_aliases: dict[str, str] = {} if replace_all else dict(load_alias_map(conn))
    for row in aliases:
        if not isinstance(row, dict):
            raise SourceRegistryError("alias row must be object")
        a = row.get("alias_key")
        c = row.get("canonical_source_key")
        if not a or not c or a == c:
            raise SourceRegistryError(f"Invalid alias row: {row!r}")
        proposed_aliases[str(a)] = str(c)
    _flatten_aliases(proposed_aliases)

    for row in overrides:
        if not isinstance(row, dict) or "source_key" not in row:
            raise SourceRegistryError(f"Invalid override row: {row!r}")
        _validate_import_override(row)

    created = updated = cleared = deleted = 0
    if dry_run:
        existing = set(list_source_keys(conn, include_superseded=True))
        for row in anchors:
            key = row.get("source_key")
            if key not in existing:
                created += 1
            else:
                updated += 1
        for row in overrides:
            # count as update/clear heuristically
            if all(row.get(f) is None for f in _OVERRIDE_FIELDS if f in row):
                cleared += 1
            else:
                updated += 1
        if replace_all:
            deleted = len(existing)
        return ImportResult(created, updated, cleared, deleted)

    try:
        conn.execute("BEGIN IMMEDIATE")
        if replace_all:
            conn.execute("DELETE FROM source_overrides")
            conn.execute("DELETE FROM source_aliases")
            deleted = 1
        now = datetime.now().astimezone()
        for row in anchors:
            key = str(row["source_key"])
            existed = conn.execute(
                "SELECT 1 FROM sources WHERE source_key = ?", (key,)
            ).fetchone()
            ensure_source_anchor(conn, key, now=now)
            if existed:
                updated += 1
            else:
                created += 1
        for row in overrides:
            key = str(row["source_key"])
            ensure_source_anchor(conn, key, now=now)
            updates = _merge_override_updates(row, merge=merge, conn=conn, key=key)
            set_overrides(
                conn, key, updates=updates, updated_by="import", now=now, commit=False
            )
            updated += 1
        for row in aliases:
            from rollup.source_registry import alias_sources

            # Use internal path without nested transaction
            alias_key = str(row["alias_key"])
            canonical = str(row["canonical_source_key"])
            ensure_source_anchor(conn, canonical, now=now)
            conn.execute(
                """INSERT INTO source_aliases (alias_key, canonical_source_key, created_at, note)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(alias_key) DO UPDATE SET
                     canonical_source_key=excluded.canonical_source_key""",
                (alias_key, canonical, now.isoformat(), row.get("note")),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ImportResult(created, updated, cleared, deleted)


_OVERRIDE_FIELDS = (
    "enabled",
    "always_surface",
    "priority",
    "newsletter_type",
    "grouping_policy",
    "summary_profile",
    "expected_cadence",
    "display_name",
    "notes",
)


def _validate_import_override(row: dict[str, Any]) -> None:
    if "newsletter_type" in row and row["newsletter_type"] is not None:
        if row["newsletter_type"] not in NEWSLETTER_TYPES:
            raise SourceRegistryError(f"Invalid newsletter_type {row['newsletter_type']!r}")
    if "grouping_policy" in row and row["grouping_policy"] is not None:
        if row["grouping_policy"] not in GROUPING_POLICIES:
            raise SourceRegistryError(f"Invalid grouping_policy {row['grouping_policy']!r}")
    if "expected_cadence" in row and row["expected_cadence"] is not None:
        if row["expected_cadence"] not in CADENCE_LABELS:
            raise SourceRegistryError(f"Invalid expected_cadence {row['expected_cadence']!r}")
    if "priority" in row and row["priority"] is not None:
        p = int(row["priority"])
        if p < 0 or p > 100:
            raise SourceRegistryError("priority must be 0..100")


def _merge_override_updates(
    row: dict[str, Any], *, merge: bool, conn: sqlite3.Connection, key: str
) -> dict[str, Any]:
    """3-valued merge: absent=unchanged, null=clear, value=set."""
    if not merge:
        updates = {f: row.get(f) for f in _OVERRIDE_FIELDS}
        return updates
    current = load_overrides(conn, key)
    updates: dict[str, Any] = {}
    for field in _OVERRIDE_FIELDS:
        if field not in row:
            updates[field] = getattr(current, field)
        else:
            updates[field] = row[field]
    # Preserve timestamps from export when present via set_overrides updated_by=import
    return updates
