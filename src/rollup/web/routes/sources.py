"""Source quality and policy routes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from flask import Blueprint, g, redirect, render_template, request, url_for

from rollup.payload_limits import (
    DEFAULT_PAGE_SIZE,
    MAX_DISPLAY_NAME_LEN,
    MAX_PAGE_SIZE,
    MAX_RECENT_SOURCE_EMAILS,
)
from rollup.source_quality import score_sources
from rollup.source_models import GROUPING_POLICIES
from rollup.source_registry import (
    NEWSLETTER_TYPES,
    SourceRegistryError,
    load_overrides,
    resolve_alias,
    set_overrides,
)
from rollup.web.csrf import validate_csrf_token as csrf_ok
from rollup.web_ids import IdError, decode_opaque, encode_opaque

bp = Blueprint("sources", __name__)


@dataclass(frozen=True)
class SourcePolicyPatch:
    fields: frozenset[str]
    display_name: str | None = None
    newsletter_type: str | None = None
    grouping_policy: str | None = None
    overrides_updated_at: str | None = None


@bp.get("/sources")
def list_sources():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    try:
        page_size = min(
            MAX_PAGE_SIZE, max(1, int(request.args.get("page_size", DEFAULT_PAGE_SIZE)))
        )
    except ValueError:
        page_size = DEFAULT_PAGE_SIZE
    offset = (page - 1) * page_size
    now = datetime.now(timezone.utc)
    # Fetch one extra page-worth to know has_next cheaply; score_sources already slices
    rows = score_sources(g.db, now=now, limit=page_size + 1, offset=offset)
    has_next = len(rows) > page_size
    rows = rows[:page_size]
    return render_template(
        "sources/list.html",
        sources=rows,
        page=page,
        page_size=page_size,
        has_prev=page > 1,
        has_next=has_next,
        encode_opaque=encode_opaque,
    )


@bp.get("/sources/<id_enc>")
def source_detail(id_enc: str):
    try:
        source_key = decode_opaque(id_enc, kind="source")
    except IdError:
        return render_template("errors/404.html", message="Invalid source id"), 404
    canonical = resolve_alias(g.db, source_key)

    obs_row = g.db.execute(
        """SELECT first_seen_at, last_seen_at, message_count_total, observed_list_id,
                  last_folder_name, last_detected_newsletter_type, cadence_label
           FROM source_observations WHERE source_key = ?""",
        (canonical,),
    ).fetchone()
    observation = None
    if obs_row:
        observation = {
            "first_seen_at": obs_row[0],
            "last_seen_at": obs_row[1],
            "message_count_total": obs_row[2],
            "observed_list_id": obs_row[3],
            "last_folder_name": obs_row[4],
            "last_detected_newsletter_type": obs_row[5],
            "cadence_label": obs_row[6],
        }
    anchor_row = g.db.execute(
        "SELECT source_key, display_name_observed, lifecycle FROM sources WHERE source_key = ?",
        (canonical,),
    ).fetchone()
    anchor = None
    if anchor_row:
        anchor = {
            "source_key": anchor_row[0],
            "display_name_observed": anchor_row[1],
            "lifecycle": anchor_row[2],
        }
    overrides = load_overrides(g.db, canonical)
    aliases = g.db.execute(
        "SELECT alias_key, note FROM source_aliases WHERE canonical_source_key = ? ORDER BY alias_key",
        (canonical,),
    ).fetchall()

    now = datetime.now(timezone.utc)
    quality_rows = [
        r for r in score_sources(g.db, now=now, limit=10_000) if r.canonical_source_key == canonical
    ]
    quality = quality_rows[0] if quality_rows else None

    recent = g.db.execute(
        """SELECT e.message_key, e.subject, e.sender, e.date_parsed, e.date_raw,
                  e.run_id, e.summary, e.primary_link
           FROM rollup_entries e
           JOIN message_source_links l ON l.message_key = e.message_key
           WHERE l.source_key_observed = ? OR l.source_key_observed IN (
             SELECT alias_key FROM source_aliases WHERE canonical_source_key = ?
           ) OR l.source_key_observed = ?
           ORDER BY COALESCE(e.date_parsed, '') DESC, e.message_key
           LIMIT ?""",
        (canonical, canonical, source_key, MAX_RECENT_SOURCE_EMAILS),
    ).fetchall()
    recent_emails = [
        {
            "message_key": r[0],
            "subject": r[1],
            "sender": r[2],
            "date_parsed": r[3],
            "date_raw": r[4],
            "run_id": r[5],
            "summary": r[6],
            "primary_link": r[7],
            "id_enc": encode_opaque(r[0]),
        }
        for r in recent
    ]

    return render_template(
        "sources/detail.html",
        source_key=canonical,
        id_enc=encode_opaque(canonical),
        observation=observation,
        overrides=overrides,
        aliases=aliases,
        quality=quality,
        recent_emails=recent_emails,
        newsletter_types=sorted(NEWSLETTER_TYPES),
        grouping_policies=sorted(GROUPING_POLICIES),
    )


@bp.post("/sources/<id_enc>/policy")
def source_policy(id_enc: str):
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    try:
        source_key = decode_opaque(id_enc, kind="source")
    except IdError:
        return render_template("errors/404.html", message="Invalid source id"), 404

    fields = {f for f in request.form.getlist("fields") if f}
    allowed = {"display_name", "newsletter_type", "grouping_policy"}
    fields &= allowed
    if not fields:
        return render_template("errors/400.html", message="No policy fields submitted"), 400

    patch = SourcePolicyPatch(
        fields=frozenset(fields),
        display_name=request.form.get("display_name"),
        newsletter_type=request.form.get("newsletter_type") or None,
        grouping_policy=request.form.get("grouping_policy") or None,
        overrides_updated_at=request.form.get("overrides_updated_at") or None,
    )
    try:
        canonical = resolve_alias(g.db, source_key)
        updates: dict = {}
        if "display_name" in patch.fields:
            name = (patch.display_name or "").strip()
            if len(name) > MAX_DISPLAY_NAME_LEN:
                return render_template("errors/400.html", message="Display name too long"), 400
            updates["display_name"] = name or None
        if "newsletter_type" in patch.fields:
            nt = patch.newsletter_type
            if nt is not None and nt not in NEWSLETTER_TYPES:
                return render_template("errors/400.html", message="Invalid newsletter type"), 400
            updates["newsletter_type"] = nt
        if "grouping_policy" in patch.fields:
            gp = patch.grouping_policy
            if gp is not None and gp not in GROUPING_POLICIES:
                return render_template("errors/400.html", message="Invalid grouping policy"), 400
            updates["grouping_policy"] = gp

        g.db.execute("BEGIN IMMEDIATE")
        # Optimistic lock must be checked after taking the write lock.
        current = load_overrides(g.db, canonical)
        if current.updated_at is not None and current.updated_at != patch.overrides_updated_at:
            g.db.rollback()
            return (
                render_template(
                    "errors/409.html",
                    message="Source policy was modified elsewhere; reload and retry.",
                ),
                409,
            )
        set_overrides(g.db, canonical, updates=updates, updated_by="web", commit=False)
        g.db.commit()
    except sqlite3.OperationalError as exc:
        g.db.rollback()
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return (
                render_template(
                    "errors/503.html",
                    message="Database busy (digest or another writer). Retry shortly.",
                ),
                503,
            )
        raise
    except SourceRegistryError as exc:
        g.db.rollback()
        return render_template("errors/400.html", message=str(exc)), 400

    return redirect(url_for("sources.source_detail", id_enc=encode_opaque(canonical)))
