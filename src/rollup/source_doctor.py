"""Doctor checks for the source registry."""

from __future__ import annotations

import sqlite3
from typing import Any

from rollup.source_models import CADENCE_LABELS, GROUPING_POLICIES
from rollup.source_registry import load_alias_map, _flatten_aliases, SourceRegistryError
from rollup.state import get_schema_version
from rollup.summary_profiles import get_builtin_summary_profile_set


def run_source_doctor(conn: sqlite3.Connection) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    ok = True

    version = get_schema_version(conn)
    if version < 7:
        checks.append(
            {
                "id": "source_registry_schema",
                "status": "fail",
                "message": f"schema version {version} < 7",
            }
        )
        ok = False
    else:
        checks.append(
            {
                "id": "source_registry_schema",
                "status": "pass",
                "message": f"schema version {version}",
            }
        )

    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        checks.append(
            {
                "id": "source_foreign_keys",
                "status": "fail",
                "message": f"{len(fk)} foreign key violations",
            }
        )
        ok = False
    else:
        checks.append(
            {
                "id": "source_foreign_keys",
                "status": "pass",
                "message": "no foreign key violations",
            }
        )

    try:
        aliases = load_alias_map(conn)
        checks.append(
            {
                "id": "source_alias_graph",
                "status": "pass",
                "message": f"{len(aliases)} aliases (acyclic)",
            }
        )
    except SourceRegistryError as exc:
        checks.append(
            {
                "id": "source_alias_graph",
                "status": "fail",
                "message": str(exc),
            }
        )
        ok = False

    # Orphan aliases
    orphans = 0
    for alias, canonical in conn.execute(
        "SELECT alias_key, canonical_source_key FROM source_aliases"
    ):
        row = conn.execute(
            "SELECT 1 FROM sources WHERE source_key = ?", (canonical,)
        ).fetchone()
        if not row:
            orphans += 1
    if orphans:
        checks.append(
            {
                "id": "source_orphan_aliases",
                "status": "fail",
                "message": f"{orphans} orphan aliases",
            }
        )
        ok = False
    else:
        checks.append(
            {
                "id": "source_orphan_aliases",
                "status": "pass",
                "message": "no orphan aliases",
            }
        )

    # Invalid enums / stale profiles
    profiles = set(get_builtin_summary_profile_set().profiles)
    stale_profiles = 0
    bad_enums = 0
    for row in conn.execute(
        "SELECT source_key, grouping_policy, expected_cadence, summary_profile, priority FROM source_overrides"
    ):
        key, gp, cad, prof, pri = row
        if gp is not None and gp not in GROUPING_POLICIES:
            bad_enums += 1
        if cad is not None and cad not in CADENCE_LABELS:
            bad_enums += 1
        if pri is not None and (pri < 0 or pri > 100):
            bad_enums += 1
        if prof and prof not in profiles:
            stale_profiles += 1
            checks.append(
                {
                    "id": "source_stale_profile",
                    "status": "warn",
                    "message": f"{key} references unknown profile {prof!r}",
                }
            )
    if bad_enums:
        checks.append(
            {
                "id": "source_invalid_enums",
                "status": "fail",
                "message": f"{bad_enums} invalid enum/range values",
            }
        )
        ok = False
    else:
        checks.append(
            {
                "id": "source_invalid_enums",
                "status": "pass",
                "message": "override enums/ranges ok",
            }
        )
    if stale_profiles == 0:
        checks.append(
            {
                "id": "source_override_profiles",
                "status": "pass",
                "message": "no stale summary profiles",
            }
        )

    # Superseded anomalies
    anomalies = 0
    for key, life, by in conn.execute(
        "SELECT source_key, lifecycle, superseded_by FROM sources WHERE lifecycle = 'superseded'"
    ):
        if not by:
            anomalies += 1
        else:
            alias = conn.execute(
                "SELECT 1 FROM source_aliases WHERE alias_key = ?", (key,)
            ).fetchone()
            if not alias:
                anomalies += 1
    checks.append(
        {
            "id": "source_superseded",
            "status": "warn" if anomalies else "pass",
            "message": (
                f"{anomalies} superseded anomalies"
                if anomalies
                else "superseded rows ok"
            ),
        }
    )

    return {
        "schema_version": 1,
        "ok": ok,
        "checks": checks,
    }
