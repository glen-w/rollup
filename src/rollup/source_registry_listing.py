"""Paginated source-registry listing and shared override form parsing."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from rollup.source_registry import SourceRegistryError, compute_source_revisions_bulk


@dataclass(frozen=True)
class SourceRegistryPageRow:
    source_key: str
    display_name: str | None
    enabled: bool
    always_surface: bool
    priority: int
    lifecycle: str
    last_seen_at: str | None
    message_count_total: int
    revision: str


@dataclass(frozen=True)
class SourceRegistryPage:
    rows: tuple[SourceRegistryPageRow, ...]
    page: int
    page_size: int
    has_next: bool
    total: int | None  # None when total is not computed (bounded)


_REGISTRY_SORTS = frozenset(
    {"last_seen_desc", "last_seen_asc", "priority_desc", "key_asc"}
)
_REGISTRY_FILTERS = frozenset({"all", "enabled", "disabled", "active"})

_OVERRIDE_FIELD_KEYS = frozenset(
    {
        "enabled",
        "always_surface",
        "priority",
        "newsletter_type",
        "grouping_policy",
        "summary_profile",
        "expected_cadence",
        "display_name",
        "notes",
    }
)
_TRI_TRUE = frozenset({"1", "true", "yes", "on"})
_TRI_FALSE = frozenset({"0", "false", "no", "off"})
_TRI_INHERIT = frozenset({"", "inherit", "default"})


def _parse_tri_bool(raw: str | None) -> bool | None:
    text = (raw or "").strip().lower()
    if text in _TRI_INHERIT:
        return None
    if text in _TRI_TRUE:
        return True
    if text in _TRI_FALSE:
        return False
    raise SourceRegistryError(f"invalid boolean {raw!r}")


def parse_override_updates(
    *,
    fields: Sequence[str] | set[str],
    values: Mapping[str, Any],
    clear_all: bool = False,
) -> dict[str, Any] | None:
    """Shared core override parser for CLI/web.

    Returns ``None`` when clear_all is set. Missing field keys mean do not touch.
    Explicit inherit / empty clears to None.
    """
    if clear_all:
        return None
    field_set = {f for f in fields if f in _OVERRIDE_FIELD_KEYS}
    updates: dict[str, Any] = {}
    if "enabled" in field_set:
        updates["enabled"] = _parse_tri_bool(
            None if values.get("enabled") is None else str(values.get("enabled"))
        )
    if "always_surface" in field_set:
        updates["always_surface"] = _parse_tri_bool(
            None
            if values.get("always_surface") is None
            else str(values.get("always_surface"))
        )
    if "priority" in field_set:
        raw = str(values.get("priority") or "").strip()
        if raw.lower() in _TRI_INHERIT:
            updates["priority"] = None
        else:
            updates["priority"] = int(raw)
    for text_field in (
        "newsletter_type",
        "grouping_policy",
        "summary_profile",
        "expected_cadence",
        "display_name",
        "notes",
    ):
        if text_field in field_set:
            raw = str(values.get(text_field) or "").strip()
            updates[text_field] = raw or None
    if not updates:
        raise SourceRegistryError("No policy fields submitted")
    return updates


def list_source_registry_page(
    conn: sqlite3.Connection,
    *,
    page: int = 1,
    page_size: int = 25,
    sort: str = "last_seen_desc",
    enabled_filter: str = "all",
    q: str | None = None,
) -> SourceRegistryPage:
    """Paginated registry summary with allowlisted filters and stable sort."""
    if sort not in _REGISTRY_SORTS:
        raise SourceRegistryError(f"invalid sort {sort!r}")
    if enabled_filter not in _REGISTRY_FILTERS:
        raise SourceRegistryError(f"invalid filter {enabled_filter!r}")
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    offset = (page - 1) * page_size

    where: list[str] = []
    params: list[Any] = []
    if enabled_filter == "all":
        pass  # every lifecycle
    elif enabled_filter == "active":
        where.append("s.lifecycle = 'active'")
    elif enabled_filter == "enabled":
        where.append("s.lifecycle = 'active'")
        where.append("(o.enabled IS NULL OR o.enabled = 1)")
    elif enabled_filter == "disabled":
        where.append("s.lifecycle = 'active'")
        where.append("o.enabled = 0")
    if q:
        where.append(
            "(s.source_key LIKE ? OR IFNULL(o.display_name, '') LIKE ? "
            "OR IFNULL(s.display_name_observed, '') LIKE ?)"
        )
        like = f"%{q.strip()[:100]}%"
        params.extend([like, like, like])

    order = {
        "last_seen_desc": "obs.last_seen_at IS NULL, obs.last_seen_at DESC, s.source_key ASC",
        "last_seen_asc": "obs.last_seen_at IS NULL, obs.last_seen_at ASC, s.source_key ASC",
        "priority_desc": "IFNULL(o.priority, 0) DESC, s.source_key ASC",
        "key_asc": "s.source_key ASC",
    }[sort]

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    # Prefer has_next over unbounded COUNT(*) on large registries.
    rows = conn.execute(
        f"""SELECT s.source_key,
                   COALESCE(o.display_name, s.display_name_observed) AS display_name,
                   CASE WHEN o.enabled IS NULL THEN 1 ELSE o.enabled END AS enabled,
                   CASE WHEN o.always_surface IS NULL THEN 0 ELSE o.always_surface END AS always_surface,
                   IFNULL(o.priority, 0) AS priority,
                   s.lifecycle,
                   obs.last_seen_at,
                   IFNULL(obs.message_count_total, 0)
            FROM sources s
            LEFT JOIN source_overrides o ON o.source_key = s.source_key
            LEFT JOIN source_observations obs ON obs.source_key = s.source_key
            {where_sql}
            ORDER BY {order}
            LIMIT ? OFFSET ?""",
        [*params, page_size + 1, offset],
    ).fetchall()
    has_next = len(rows) > page_size
    rows = rows[:page_size]
    keys = [r[0] for r in rows]
    revisions = compute_source_revisions_bulk(conn, keys) if keys else {}
    out: list[SourceRegistryPageRow] = []
    for r in rows:
        key = r[0]
        out.append(
            SourceRegistryPageRow(
                source_key=key,
                display_name=r[1],
                enabled=bool(r[2]),
                always_surface=bool(r[3]),
                priority=int(r[4]),
                lifecycle=r[5],
                last_seen_at=r[6],
                message_count_total=int(r[7]),
                revision=revisions[key],
            )
        )
    return SourceRegistryPage(
        rows=tuple(out),
        page=page,
        page_size=page_size,
        has_next=has_next,
        total=None,
    )
