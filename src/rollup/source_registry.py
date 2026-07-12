"""SQLite repository for the newsletter source registry."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from rollup.models import NewsletterType, ParsedMessage
from rollup.source_cadence import estimate_cadence
from rollup.source_identity import (
    extract_display_name,
    normalize_email,
    normalize_from_addr,
    normalize_list_id,
)
from rollup.source_models import (
    CADENCE_LABELS,
    CADENCE_SAMPLE_RETENTION,
    GROUPING_POLICIES,
    CadenceEstimate,
    CadenceLabel,
    GroupingPolicy,
    SourceObservation,
    SourceOverrides,
    SourcePolicy,
    SourceRecord,
    SourceRegistrySnapshot,
    empty_defaults_snapshot,
)
from rollup.source_policy import resolve_source_policy
from rollup.state import ensure_source_registry_schema, get_schema_version

logger = logging.getLogger(__name__)

NEWSLETTER_TYPES = frozenset(
    {
        "short_update",
        "multi_section_digest",
        "essay",
        "link_roundup",
        "unclassified",
    }
)


class SourceRegistryError(ValueError):
    """Invalid source registry operation."""


class AmbiguousSourceRef(SourceRegistryError):
    def __init__(self, ref: str, candidates: Sequence[str]) -> None:
        self.ref = ref
        self.candidates = tuple(sorted(candidates))
        super().__init__(
            f"Ambiguous source reference {ref!r}; candidates: {', '.join(self.candidates)}"
        )


class SourceNotFound(SourceRegistryError):
    def __init__(self, ref: str) -> None:
        self.ref = ref
        super().__init__(f"Unknown source reference {ref!r}")


@dataclass(frozen=True)
class ObserveResult:
    discovered_this_run: int
    messages_unidentifiable: int
    touched_keys: tuple[str, ...]


def _now_iso(when: datetime | None = None) -> str:
    return (when or datetime.now().astimezone()).isoformat()


def ensure_registry(conn: sqlite3.Connection) -> None:
    ensure_source_registry_schema(conn)


def _canonical_json_addrs(addrs: Iterable[str]) -> str:
    return json.dumps(sorted(set(addrs)), separators=(",", ":"), ensure_ascii=False)


def load_alias_map(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT alias_key, canonical_source_key FROM source_aliases"
    ).fetchall()
    raw = {a: c for a, c in rows}
    return _flatten_aliases(raw)


def _flatten_aliases(raw: Mapping[str, str], *, max_depth: int = 8) -> dict[str, str]:
    flat: dict[str, str] = {}
    for alias in raw:
        seen: list[str] = []
        cur = alias
        depth = 0
        while cur in raw:
            if cur in seen:
                raise SourceRegistryError(f"Alias cycle involving {alias!r}")
            seen.append(cur)
            cur = raw[cur]
            depth += 1
            if depth > max_depth:
                raise SourceRegistryError(f"Alias chain too deep for {alias!r}")
        flat[alias] = cur
    return flat


def resolve_alias(conn: sqlite3.Connection, source_key: str) -> str:
    aliases = load_alias_map(conn)
    return aliases.get(source_key, source_key)


def ensure_source_anchor(
    conn: sqlite3.Connection,
    source_key: str,
    *,
    now: datetime | None = None,
    display_name_observed: str | None = None,
) -> bool:
    """Insert source anchor if missing. Returns True if newly created."""
    iso = _now_iso(now)
    row = conn.execute(
        "SELECT source_key FROM sources WHERE source_key = ?", (source_key,)
    ).fetchone()
    if row:
        if display_name_observed:
            conn.execute(
                """UPDATE sources SET display_name_observed = COALESCE(display_name_observed, ?),
                   updated_at = ? WHERE source_key = ? AND display_name_observed IS NULL""",
                (display_name_observed, iso, source_key),
            )
        return False
    conn.execute(
        """INSERT INTO sources
           (source_key, identity_version, lifecycle, superseded_by,
            display_name_observed, created_at, updated_at)
           VALUES (?, 1, 'active', NULL, ?, ?, ?)""",
        (source_key, display_name_observed, iso, iso),
    )
    return True


def observe_sources(
    conn: sqlite3.Connection,
    messages: Sequence[ParsedMessage],
    *,
    generated_at: datetime,
    detected_types: Mapping[str, str] | None = None,
) -> ObserveResult:
    """Aggregate observations and write in one transaction (idempotent by message_key)."""
    ensure_registry(conn)
    aliases = load_alias_map(conn)
    detected_types = detected_types or {}
    unidentifiable = 0
    by_source: dict[str, list[ParsedMessage]] = defaultdict(list)
    for msg in messages:
        if not msg.source_key:
            unidentifiable += 1
            continue
        key = aliases.get(msg.source_key, msg.source_key)
        by_source[key].append(msg)

    discovered = 0
    touched: list[str] = []
    iso = _now_iso(generated_at)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for source_key, msgs in sorted(by_source.items()):
            created = ensure_source_anchor(
                conn,
                source_key,
                now=generated_at,
                display_name_observed=extract_display_name(msgs[-1].sender),
            )
            if created:
                discovered += 1
            touched.append(source_key)
            _observe_one_source(
                conn,
                source_key,
                msgs,
                generated_at=generated_at,
                iso=iso,
                detected_types=detected_types,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ObserveResult(
        discovered_this_run=discovered,
        messages_unidentifiable=unidentifiable,
        touched_keys=tuple(touched),
    )


def _observe_one_source(
    conn: sqlite3.Connection,
    source_key: str,
    msgs: list[ParsedMessage],
    *,
    generated_at: datetime,
    iso: str,
    detected_types: Mapping[str, str],
) -> None:
    new_count = 0
    from_addrs: set[str] = set()
    list_ids: list[str] = []
    last_folder = None
    last_detected = None
    last_family = None
    display = None
    for msg in msgs:
        addr = normalize_from_addr(msg.sender)
        if addr:
            from_addrs.add(addr)
        if msg.list_id:
            list_ids.append(msg.list_id)
        last_folder = msg.folder_name
        last_detected = detected_types.get(msg.message_key) or last_detected
        last_family = msg.subject
        display = extract_display_name(msg.sender) or display
        cur = conn.execute(
            """INSERT OR IGNORE INTO source_observation_dedup
               (source_key, message_key, first_observed_at) VALUES (?, ?, ?)""",
            (source_key, msg.message_key, iso),
        )
        if cur.rowcount:
            new_count += 1
        if msg.date_parsed is not None:
            conn.execute(
                """INSERT INTO source_cadence_samples (source_key, message_key, date_parsed)
                   VALUES (?, ?, ?)
                   ON CONFLICT(source_key, message_key) DO UPDATE SET
                     date_parsed=excluded.date_parsed""",
                (source_key, msg.message_key, msg.date_parsed.isoformat()),
            )

    _retain_cadence_samples(conn, source_key)
    cadence = _recompute_cadence_cache(conn, source_key, calculated_at=iso)

    existing = None  # reserved
    obs = conn.execute(
        "SELECT first_seen_at, message_count_total, observed_from_addrs_json FROM source_observations WHERE source_key = ?",
        (source_key,),
    ).fetchone()
    src = conn.execute(
        "SELECT display_name_observed FROM sources WHERE source_key = ?",
        (source_key,),
    ).fetchone()

    if obs is None:
        first = iso
        total = new_count
        addrs = sorted(from_addrs)
    else:
        first = obs[0]
        total = int(obs[1]) + new_count
        try:
            prev = set(json.loads(obs[2] or "[]"))
        except json.JSONDecodeError:
            prev = set()
        addrs = sorted(prev | from_addrs)

    observed_list = list_ids[-1] if list_ids else None
    conn.execute(
        """INSERT INTO source_observations (
            source_key, first_seen_at, last_seen_at, message_count_total,
            observed_from_addrs_json, observed_list_id, last_folder_name,
            last_detected_newsletter_type, cadence_label, cadence_confidence,
            cadence_sample_count, cadence_median_hours, cadence_calculated_at,
            last_subject_family
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_key) DO UPDATE SET
             last_seen_at=excluded.last_seen_at,
             message_count_total=excluded.message_count_total,
             observed_from_addrs_json=excluded.observed_from_addrs_json,
             observed_list_id=COALESCE(excluded.observed_list_id, source_observations.observed_list_id),
             last_folder_name=excluded.last_folder_name,
             last_detected_newsletter_type=COALESCE(excluded.last_detected_newsletter_type, source_observations.last_detected_newsletter_type),
             cadence_label=excluded.cadence_label,
             cadence_confidence=excluded.cadence_confidence,
             cadence_sample_count=excluded.cadence_sample_count,
             cadence_median_hours=excluded.cadence_median_hours,
             cadence_calculated_at=excluded.cadence_calculated_at,
             last_subject_family=excluded.last_subject_family
        """,
        (
            source_key,
            first,
            iso,
            total,
            _canonical_json_addrs(addrs),
            observed_list,
            last_folder,
            last_detected,
            cadence.label,
            cadence.confidence,
            cadence.sample_count,
            cadence.median_hours,
            iso,
            last_family,
        ),
    )
    if display and (src is None or src[0] is None):
        conn.execute(
            "UPDATE sources SET display_name_observed = ?, updated_at = ? WHERE source_key = ?",
            (display, iso, source_key),
        )
    conn.execute(
        "UPDATE sources SET updated_at = ? WHERE source_key = ?",
        (iso, source_key),
    )
    del existing
    del generated_at


def _retain_cadence_samples(conn: sqlite3.Connection, source_key: str) -> None:
    rows = conn.execute(
        """SELECT message_key FROM source_cadence_samples
           WHERE source_key = ?
           ORDER BY date_parsed DESC, message_key DESC""",
        (source_key,),
    ).fetchall()
    if len(rows) <= CADENCE_SAMPLE_RETENTION:
        return
    drop = [r[0] for r in rows[CADENCE_SAMPLE_RETENTION:]]
    conn.executemany(
        "DELETE FROM source_cadence_samples WHERE source_key = ? AND message_key = ?",
        [(source_key, mk) for mk in drop],
    )


def _recompute_cadence_cache(
    conn: sqlite3.Connection, source_key: str, *, calculated_at: str
) -> CadenceEstimate:
    rows = conn.execute(
        """SELECT date_parsed FROM source_cadence_samples
           WHERE source_key = ? ORDER BY date_parsed ASC""",
        (source_key,),
    ).fetchall()
    dates: list[datetime] = []
    for (raw,) in rows:
        try:
            dates.append(datetime.fromisoformat(raw))
        except ValueError:
            continue
    estimate = estimate_cadence(dates)
    # Persist happens in caller upsert; return for use.
    del calculated_at
    return estimate


def load_overrides(conn: sqlite3.Connection, source_key: str) -> SourceOverrides:
    row = conn.execute(
        """SELECT enabled, always_surface, priority, newsletter_type, grouping_policy,
                  summary_profile, expected_cadence, display_name, notes, updated_at, updated_by
           FROM source_overrides WHERE source_key = ?""",
        (source_key,),
    ).fetchone()
    if not row:
        return SourceOverrides()
    return _row_to_overrides(row)


def _row_to_overrides(row: tuple[Any, ...]) -> SourceOverrides:
    (
        enabled,
        always_surface,
        priority,
        newsletter_type,
        grouping_policy,
        summary_profile,
        expected_cadence,
        display_name,
        notes,
        updated_at,
        updated_by,
    ) = row
    nt: NewsletterType | None = None
    if newsletter_type in NEWSLETTER_TYPES:
        nt = newsletter_type  # type: ignore[assignment]
    gp: GroupingPolicy | None = None
    if grouping_policy in GROUPING_POLICIES:
        gp = grouping_policy  # type: ignore[assignment]
    cad: CadenceLabel | None = None
    if expected_cadence in CADENCE_LABELS:
        cad = expected_cadence  # type: ignore[assignment]
    return SourceOverrides(
        enabled=None if enabled is None else bool(enabled),
        always_surface=None if always_surface is None else bool(always_surface),
        priority=priority,
        newsletter_type=nt,
        grouping_policy=gp,
        summary_profile=summary_profile,
        expected_cadence=cad,
        display_name=display_name,
        notes=notes,
        updated_at=updated_at,
        updated_by=updated_by,
    )


def load_observation(conn: sqlite3.Connection, source_key: str) -> SourceObservation:
    row = conn.execute(
        """SELECT first_seen_at, last_seen_at, message_count_total, observed_from_addrs_json,
                  observed_list_id, last_folder_name, last_detected_newsletter_type,
                  cadence_label, cadence_confidence, cadence_sample_count,
                  cadence_median_hours, cadence_calculated_at, last_subject_family
           FROM source_observations WHERE source_key = ?""",
        (source_key,),
    ).fetchone()
    src = conn.execute(
        "SELECT display_name_observed FROM sources WHERE source_key = ?",
        (source_key,),
    ).fetchone()
    display = src[0] if src else None
    if not row:
        return SourceObservation(display_name_observed=display)
    try:
        addrs = tuple(json.loads(row[3] or "[]"))
    except json.JSONDecodeError:
        addrs = ()
    label = row[7] if row[7] in CADENCE_LABELS else "unknown"
    return SourceObservation(
        first_seen_at=row[0],
        last_seen_at=row[1],
        message_count_total=int(row[2]),
        observed_from_addrs=addrs,
        observed_list_id=row[4],
        last_folder_name=row[5],
        last_detected_newsletter_type=row[6],
        cadence=CadenceEstimate(
            label,  # type: ignore[arg-type]
            float(row[8]),
            int(row[9]),
            row[10],
        ),
        display_name_observed=display,
        last_subject_family=row[12],
        cadence_calculated_at=row[11],
    )


def load_SourceRegistrySnapshot(
    conn: sqlite3.Connection,
    source_keys: set[str] | None = None,
    *,
    discovered_this_run: int = 0,
    messages_unidentifiable_source: int = 0,
) -> SourceRegistrySnapshot:
    ensure_registry(conn)
    aliases = load_alias_map(conn)
    known = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    if source_keys is None:
        keys = [
            r[0]
            for r in conn.execute(
                "SELECT source_key FROM sources WHERE lifecycle = 'active'"
            ).fetchall()
        ]
    else:
        keys = sorted({aliases.get(k, k) for k in source_keys if k})
    policies: dict[str, SourcePolicy] = {}
    for key in keys:
        row = conn.execute(
            "SELECT lifecycle FROM sources WHERE source_key = ?", (key,)
        ).fetchone()
        if not row:
            continue
        obs = load_observation(conn, key)
        overrides = load_overrides(conn, key)
        policies[key] = resolve_source_policy(
            key, obs, overrides, lifecycle=row[0]
        )
    revision = compute_policy_state_revision(conn)
    return SourceRegistrySnapshot(
        policies=policies,
        aliases=aliases,
        known_count=int(known),
        discovered_this_run=discovered_this_run,
        registry_schema_version=get_schema_version(conn),
        policy_state_revision=revision,
        messages_unidentifiable_source=messages_unidentifiable_source,
    )


def compute_policy_state_revision(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """SELECT source_key, enabled, always_surface, priority, newsletter_type,
                  grouping_policy, summary_profile, expected_cadence, display_name, notes
           FROM source_overrides ORDER BY source_key"""
    ).fetchall()
    aliases = conn.execute(
        "SELECT alias_key, canonical_source_key FROM source_aliases ORDER BY alias_key"
    ).fetchall()
    payload = json.dumps({"o": rows, "a": aliases}, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def set_overrides(
    conn: sqlite3.Connection,
    source_key: str,
    *,
    updates: Mapping[str, Any],
    updated_by: str = "cli",
    now: datetime | None = None,
    commit: bool = True,
) -> SourceOverrides:
    """Partial update. Use explicit None in updates to clear a field."""
    ensure_registry(conn)
    canonical = resolve_alias(conn, source_key)
    ensure_source_anchor(conn, canonical, now=now)
    current = load_overrides(conn, canonical)
    data = {
        "enabled": current.enabled,
        "always_surface": current.always_surface,
        "priority": current.priority,
        "newsletter_type": current.newsletter_type,
        "grouping_policy": current.grouping_policy,
        "summary_profile": current.summary_profile,
        "expected_cadence": current.expected_cadence,
        "display_name": current.display_name,
        "notes": current.notes,
    }
    for key, value in updates.items():
        if key not in data:
            raise SourceRegistryError(f"Unknown override field {key!r}")
        data[key] = value
    _validate_override_values(data)
    iso = _now_iso(now)
    conn.execute(
        """INSERT INTO source_overrides (
            source_key, enabled, always_surface, priority, newsletter_type,
            grouping_policy, summary_profile, expected_cadence, display_name, notes,
            updated_at, updated_by
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_key) DO UPDATE SET
             enabled=excluded.enabled,
             always_surface=excluded.always_surface,
             priority=excluded.priority,
             newsletter_type=excluded.newsletter_type,
             grouping_policy=excluded.grouping_policy,
             summary_profile=excluded.summary_profile,
             expected_cadence=excluded.expected_cadence,
             display_name=excluded.display_name,
             notes=excluded.notes,
             updated_at=excluded.updated_at,
             updated_by=excluded.updated_by
        """,
        (
            canonical,
            None if data["enabled"] is None else int(bool(data["enabled"])),
            None
            if data["always_surface"] is None
            else int(bool(data["always_surface"])),
            data["priority"],
            data["newsletter_type"],
            data["grouping_policy"],
            data["summary_profile"],
            data["expected_cadence"],
            data["display_name"],
            data["notes"],
            iso,
            updated_by,
        ),
    )
    if commit:
        conn.commit()
    return load_overrides(conn, canonical)


def clear_overrides(
    conn: sqlite3.Connection,
    source_key: str,
    fields: Sequence[str] | None = None,
    *,
    updated_by: str = "cli",
    now: datetime | None = None,
) -> SourceOverrides:
    canonical = resolve_alias(conn, source_key)
    if fields is None or "all" in fields:
        updates = {
            "enabled": None,
            "always_surface": None,
            "priority": None,
            "newsletter_type": None,
            "grouping_policy": None,
            "summary_profile": None,
            "expected_cadence": None,
            "display_name": None,
            "notes": None,
        }
    else:
        updates = {f: None for f in fields}
    return set_overrides(
        conn, canonical, updates=updates, updated_by=updated_by, now=now
    )


def _validate_override_values(data: Mapping[str, Any]) -> None:
    if data.get("newsletter_type") is not None and data["newsletter_type"] not in NEWSLETTER_TYPES:
        raise SourceRegistryError(f"Invalid newsletter_type {data['newsletter_type']!r}")
    if data.get("grouping_policy") is not None and data["grouping_policy"] not in GROUPING_POLICIES:
        raise SourceRegistryError(f"Invalid grouping_policy {data['grouping_policy']!r}")
    if data.get("expected_cadence") is not None and data["expected_cadence"] not in CADENCE_LABELS:
        raise SourceRegistryError(f"Invalid expected_cadence {data['expected_cadence']!r}")
    if data.get("priority") is not None:
        p = int(data["priority"])
        if p < 0 or p > 100:
            raise SourceRegistryError("priority must be 0..100")
    if data.get("display_name") is not None:
        from rollup.source_identity import validate_display_name_override

        validate_display_name_override(str(data["display_name"]))


def alias_sources(
    conn: sqlite3.Connection,
    alias_key: str,
    canonical_key: str,
    *,
    note: str | None = None,
    now: datetime | None = None,
) -> None:
    """Create alias; atomically merge if alias_key is an existing source."""
    ensure_registry(conn)
    if alias_key == canonical_key:
        raise SourceRegistryError("Cannot alias a source to itself")
    iso = _now_iso(now)
    try:
        conn.execute("BEGIN IMMEDIATE")
        ensure_source_anchor(conn, canonical_key, now=now)
        existing_alias_row = conn.execute(
            "SELECT source_key, lifecycle FROM sources WHERE source_key = ?",
            (alias_key,),
        ).fetchone()
        if existing_alias_row and existing_alias_row[0] == alias_key:
            _merge_source_into(conn, alias_key, canonical_key, iso=iso)
        # Validate graph
        proposed = dict(load_alias_map(conn))
        proposed[alias_key] = canonical_key
        _flatten_aliases(proposed)
        conn.execute(
            """INSERT INTO source_aliases (alias_key, canonical_source_key, created_at, note)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(alias_key) DO UPDATE SET
                 canonical_source_key=excluded.canonical_source_key,
                 note=excluded.note""",
            (alias_key, canonical_key, iso, note),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _merge_source_into(
    conn: sqlite3.Connection, alias_key: str, canonical_key: str, *, iso: str
) -> None:
    # Move dedup rows
    rows = conn.execute(
        "SELECT message_key, first_observed_at FROM source_observation_dedup WHERE source_key = ?",
        (alias_key,),
    ).fetchall()
    for mk, first in rows:
        conn.execute(
            """INSERT OR IGNORE INTO source_observation_dedup
               (source_key, message_key, first_observed_at) VALUES (?, ?, ?)""",
            (canonical_key, mk, first),
        )
    conn.execute(
        "DELETE FROM source_observation_dedup WHERE source_key = ?", (alias_key,)
    )
    # Move cadence samples
    samples = conn.execute(
        "SELECT message_key, date_parsed FROM source_cadence_samples WHERE source_key = ?",
        (alias_key,),
    ).fetchall()
    for mk, dp in samples:
        conn.execute(
            """INSERT INTO source_cadence_samples (source_key, message_key, date_parsed)
               VALUES (?, ?, ?)
               ON CONFLICT(source_key, message_key) DO UPDATE SET
                 date_parsed=excluded.date_parsed
               WHERE excluded.date_parsed > source_cadence_samples.date_parsed""",
            (canonical_key, mk, dp),
        )
    conn.execute(
        "DELETE FROM source_cadence_samples WHERE source_key = ?", (alias_key,)
    )
    _retain_cadence_samples(conn, canonical_key)
    cadence = _recompute_cadence_cache(conn, canonical_key, calculated_at=iso)

    # Overrides: canonical wins; fill NULLs from alias
    alias_ov = load_overrides(conn, alias_key)
    can_ov = load_overrides(conn, canonical_key)
    merged = {
        "enabled": can_ov.enabled if can_ov.enabled is not None else alias_ov.enabled,
        "always_surface": can_ov.always_surface
        if can_ov.always_surface is not None
        else alias_ov.always_surface,
        "priority": can_ov.priority if can_ov.priority is not None else alias_ov.priority,
        "newsletter_type": can_ov.newsletter_type or alias_ov.newsletter_type,
        "grouping_policy": can_ov.grouping_policy or alias_ov.grouping_policy,
        "summary_profile": can_ov.summary_profile or alias_ov.summary_profile,
        "expected_cadence": can_ov.expected_cadence or alias_ov.expected_cadence,
        "display_name": can_ov.display_name or alias_ov.display_name,
        "notes": can_ov.notes or alias_ov.notes,
    }
    conn.execute("DELETE FROM source_overrides WHERE source_key = ?", (alias_key,))
    if any(v is not None for v in merged.values()):
        conn.execute(
            """INSERT INTO source_overrides (
                source_key, enabled, always_surface, priority, newsletter_type,
                grouping_policy, summary_profile, expected_cadence, display_name, notes,
                updated_at, updated_by
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cli')
               ON CONFLICT(source_key) DO UPDATE SET
                 enabled=excluded.enabled,
                 always_surface=excluded.always_surface,
                 priority=excluded.priority,
                 newsletter_type=excluded.newsletter_type,
                 grouping_policy=excluded.grouping_policy,
                 summary_profile=excluded.summary_profile,
                 expected_cadence=excluded.expected_cadence,
                 display_name=excluded.display_name,
                 notes=excluded.notes,
                 updated_at=excluded.updated_at
            """,
            (
                canonical_key,
                None if merged["enabled"] is None else int(bool(merged["enabled"])),
                None
                if merged["always_surface"] is None
                else int(bool(merged["always_surface"])),
                merged["priority"],
                merged["newsletter_type"],
                merged["grouping_policy"],
                merged["summary_profile"],
                merged["expected_cadence"],
                merged["display_name"],
                merged["notes"],
                iso,
            ),
        )

    total = conn.execute(
        "SELECT COUNT(*) FROM source_observation_dedup WHERE source_key = ?",
        (canonical_key,),
    ).fetchone()[0]
    alias_obs = load_observation(conn, alias_key)
    can_obs = load_observation(conn, canonical_key)
    addrs = sorted(set(alias_obs.observed_from_addrs) | set(can_obs.observed_from_addrs))
    first = min(
        x
        for x in (alias_obs.first_seen_at, can_obs.first_seen_at, iso)
        if x
    )
    last = max(
        x
        for x in (alias_obs.last_seen_at, can_obs.last_seen_at, iso)
        if x
    )
    conn.execute(
        """INSERT INTO source_observations (
            source_key, first_seen_at, last_seen_at, message_count_total,
            observed_from_addrs_json, observed_list_id, last_folder_name,
            last_detected_newsletter_type, cadence_label, cadence_confidence,
            cadence_sample_count, cadence_median_hours, cadence_calculated_at,
            last_subject_family
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_key) DO UPDATE SET
             first_seen_at=excluded.first_seen_at,
             last_seen_at=excluded.last_seen_at,
             message_count_total=excluded.message_count_total,
             observed_from_addrs_json=excluded.observed_from_addrs_json,
             cadence_label=excluded.cadence_label,
             cadence_confidence=excluded.cadence_confidence,
             cadence_sample_count=excluded.cadence_sample_count,
             cadence_median_hours=excluded.cadence_median_hours,
             cadence_calculated_at=excluded.cadence_calculated_at
        """,
        (
            canonical_key,
            first,
            last,
            int(total),
            _canonical_json_addrs(addrs),
            can_obs.observed_list_id or alias_obs.observed_list_id,
            can_obs.last_folder_name or alias_obs.last_folder_name,
            can_obs.last_detected_newsletter_type
            or alias_obs.last_detected_newsletter_type,
            cadence.label,
            cadence.confidence,
            cadence.sample_count,
            cadence.median_hours,
            iso,
            can_obs.last_subject_family or alias_obs.last_subject_family,
        ),
    )
    conn.execute("DELETE FROM source_observations WHERE source_key = ?", (alias_key,))
    conn.execute(
        """UPDATE sources SET lifecycle = 'superseded', superseded_by = ?, updated_at = ?
           WHERE source_key = ?""",
        (canonical_key, iso, alias_key),
    )


def resolve_source_ref(conn: sqlite3.Connection, ref: str) -> str:
    """Resolve a user reference to a canonical source_key."""
    ensure_registry(conn)
    ref = (ref or "").strip()
    if not ref:
        raise SourceNotFound(ref)
    aliases = load_alias_map(conn)
    # Exact key or alias
    if conn.execute(
        "SELECT 1 FROM sources WHERE source_key = ?", (ref,)
    ).fetchone():
        return aliases.get(ref, ref)
    if ref in aliases:
        return aliases[ref]

    # Unique email / list-id match
    candidates: set[str] = set()
    email = normalize_from_addr(ref) or normalize_email(ref)
    lid = normalize_list_id(ref)
    if email:
        for (key,) in conn.execute("SELECT source_key FROM sources").fetchall():
            if key == f"from:{email}" or key.endswith(":" + email):
                candidates.add(aliases.get(key, key))
        for (key, raw) in conn.execute(
            "SELECT source_key, observed_from_addrs_json FROM source_observations"
        ).fetchall():
            try:
                addrs = json.loads(raw or "[]")
            except json.JSONDecodeError:
                addrs = []
            if email in addrs:
                candidates.add(aliases.get(key, key))
    if lid:
        want = f"list:{lid}"
        for (key,) in conn.execute("SELECT source_key FROM sources").fetchall():
            if key == want or key.endswith(":" + lid):
                candidates.add(aliases.get(key, key))
    if len(candidates) == 1:
        return next(iter(candidates))
    if len(candidates) > 1:
        raise AmbiguousSourceRef(ref, sorted(candidates))

    # Unique suffix ≥ 8
    if len(ref) >= 8:
        suffix_hits = [
            aliases.get(key, key)
            for (key,) in conn.execute("SELECT source_key FROM sources").fetchall()
            if key.endswith(ref)
        ]
        suffix_hits = sorted(set(suffix_hits))
        if len(suffix_hits) == 1:
            return suffix_hits[0]
        if len(suffix_hits) > 1:
            raise AmbiguousSourceRef(ref, suffix_hits)

    raise SourceNotFound(ref)


def list_source_keys(
    conn: sqlite3.Connection, *, include_superseded: bool = False
) -> list[str]:
    if include_superseded:
        rows = conn.execute(
            "SELECT source_key FROM sources ORDER BY updated_at DESC, source_key"
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT source_key FROM sources WHERE lifecycle = 'active'
               ORDER BY updated_at DESC, source_key"""
        ).fetchall()
    return [r[0] for r in rows]


def get_source_record(conn: sqlite3.Connection, source_key: str) -> SourceRecord:
    canonical = resolve_alias(conn, source_key)
    row = conn.execute(
        "SELECT lifecycle, superseded_by FROM sources WHERE source_key = ?",
        (canonical,),
    ).fetchone()
    if not row:
        raise SourceNotFound(source_key)
    obs = load_observation(conn, canonical)
    overrides = load_overrides(conn, canonical)
    policy = resolve_source_policy(canonical, obs, overrides, lifecycle=row[0])
    return SourceRecord(
        source_key=canonical,
        observation=obs,
        overrides=overrides,
        policy=policy,
        lifecycle=row[0],
        superseded_by=row[1],
    )


# Re-export for dry-run defaults
__all__ = [
    "AmbiguousSourceRef",
    "ObserveResult",
    "SourceNotFound",
    "SourceRegistryError",
    "alias_sources",
    "clear_overrides",
    "compute_policy_state_revision",
    "empty_defaults_snapshot",
    "ensure_registry",
    "ensure_source_anchor",
    "get_source_record",
    "list_source_keys",
    "load_SourceRegistrySnapshot",
    "load_alias_map",
    "observe_sources",
    "resolve_alias",
    "resolve_source_ref",
    "set_overrides",
]
