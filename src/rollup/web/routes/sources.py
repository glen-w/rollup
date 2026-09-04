"""Source quality and registry management routes."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, current_app, g, redirect, render_template, request, url_for

from rollup.effort import resolve_profile_set
from rollup.payload_limits import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from rollup.run_lock import RunLockError, acquire_state_lock
from rollup.source_models import CADENCE_LABELS, GROUPING_POLICIES
from rollup.source_quality import score_sources
from rollup.source_registry import (
    NEWSLETTER_TYPES,
    SourceNotFound,
    SourceRegistryError,
    alias_sources,
    clear_overrides,
    compute_source_revision,
    get_source_record,
    list_source_registry_page,
    parse_override_updates,
    resolve_alias,
    set_overrides,
)
from rollup.web.csrf import validate_csrf_token as csrf_ok
from rollup.web.db import mutation_connection
from rollup.web.maintenance_tokens import (
    consume_maintenance_token,
    fingerprint_parts,
    issue_maintenance_token,
)
from rollup.web_ids import IdError, decode_opaque, encode_opaque

bp = Blueprint("sources", __name__)

_BULK_MAX = 50


def _effective_summary_profiles() -> frozenset[str]:
    """Profile names from the effective configured registry (builtins + overlay)."""
    try:
        profile_set = resolve_profile_set(summary_profile_set_path=None)
        return frozenset(profile_set.profiles)
    except Exception:
        from rollup.summary_profiles import get_builtin_summary_profile_set

        return frozenset(get_builtin_summary_profile_set().profiles)


def _scholar_banner(record) -> tuple[bool, str]:
    from rollup.scholar.detect import is_scholar_source_key
    from rollup.web.config import load_web_config_document

    alert = is_scholar_source_key(
        record.source_key, record.observation.observed_list_id
    )
    mode = "default"
    try:
        mode = load_web_config_document().loaded.scholar.mode
    except Exception:
        mode = "default"
    return alert, mode


def parse_override_form(form) -> dict | None:
    """Adapt Flask form → core parse_override_updates."""
    return parse_override_updates(
        fields=form.getlist("fields"),
        values={k: form.get(k) for k in form.keys()},
        clear_all=bool(form.get("clear_all")),
    )


@bp.get("/sources")
def list_sources():
    """Quality ranking (content browsing) — separate from registry management."""
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
    rows = score_sources(g.db_ro, now=now, limit=page_size + 1, offset=offset)
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
        view="quality",
    )


@bp.get("/sources/registry")
def list_registry():
    """Operational registry list — paginated SQL, no N+1 get_source_record."""
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
    sort = request.args.get("sort", "last_seen_desc")
    enabled_filter = request.args.get("filter", "all")
    q = request.args.get("q") or None
    try:
        result = list_source_registry_page(
            g.db_ro,
            page=page,
            page_size=page_size,
            sort=sort,
            enabled_filter=enabled_filter,
            q=q,
        )
    except SourceRegistryError as exc:
        return render_template("errors/400.html", message=str(exc)), 400
    return render_template(
        "sources/registry.html",
        page_result=result,
        sort=sort,
        enabled_filter=enabled_filter,
        q=q or "",
        encode_opaque=encode_opaque,
        view="registry",
    )


@bp.get("/sources/<id_enc>")
def source_detail(id_enc: str):
    try:
        source_key = decode_opaque(id_enc, kind="source")
    except IdError:
        return render_template("errors/404.html", message="Invalid source id"), 404
    try:
        record = get_source_record(g.db_ro, source_key)
        revision = compute_source_revision(g.db_ro, record.source_key)
    except SourceNotFound:
        return render_template("errors/404.html", message="Source not found"), 404
    aliases = g.db_ro.execute(
        "SELECT alias_key, note FROM source_aliases WHERE canonical_source_key = ? "
        "ORDER BY alias_key",
        (record.source_key,),
    ).fetchall()
    scholar_alert, scholar_mode = _scholar_banner(record)
    return render_template(
        "sources/detail.html",
        record=record,
        revision=revision,
        aliases=aliases,
        id_enc=encode_opaque(record.source_key),
        newsletter_types=sorted(NEWSLETTER_TYPES),
        grouping_policies=sorted(GROUPING_POLICIES),
        cadence_labels=sorted(CADENCE_LABELS),
        summary_profiles=sorted(_effective_summary_profiles()),
        alias_preview=None,
        scholar_alert=scholar_alert,
        scholar_mode=scholar_mode,
    )


@bp.post("/sources/<id_enc>/policy")
def source_policy(id_enc: str):
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    try:
        source_key = decode_opaque(id_enc, kind="source")
    except IdError:
        return render_template("errors/404.html", message="Invalid source id"), 404

    expected_revision = request.form.get("source_revision") or ""
    profiles = _effective_summary_profiles()
    try:
        updates = parse_override_form(request.form)
    except (SourceRegistryError, ValueError) as exc:
        return render_template("errors/400.html", message=str(exc)), 400

    state_dir = Path(current_app.config["STATE_DIR"])
    try:
        lock = acquire_state_lock(
            state_dir, run_id=str(uuid.uuid4()), operation="web-source-policy"
        )
    except RunLockError:
        return (
            render_template(
                "errors/503.html",
                message="Database busy (digest or another writer). Retry shortly.",
            ),
            503,
        )
    try:
        with mutation_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                record = get_source_record(conn, source_key)
            except SourceNotFound:
                conn.rollback()
                return render_template("errors/404.html", message="Source not found"), 404
            current_rev = compute_source_revision(conn, record.source_key)
            if current_rev != expected_revision:
                conn.rollback()
                return (
                    render_template(
                        "errors/409.html",
                        message="Source was modified elsewhere; reload and retry.",
                    ),
                    409,
                )
            try:
                if updates is None:
                    clear_overrides(
                        conn,
                        record.source_key,
                        fields=["all"],
                        updated_by="web",
                        commit=False,
                    )
                else:
                    set_overrides(
                        conn,
                        record.source_key,
                        updates=updates,
                        updated_by="web",
                        commit=False,
                        summary_profile_names=profiles,
                    )
                conn.commit()
            except SourceRegistryError as exc:
                conn.rollback()
                return render_template("errors/400.html", message=str(exc)), 400
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return (
                render_template(
                    "errors/503.html",
                    message="Database busy (digest or another writer). Retry shortly.",
                ),
                503,
            )
        raise
    finally:
        lock.release()

    return redirect(url_for("sources.source_detail", id_enc=encode_opaque(record.source_key)))


@bp.post("/sources/bulk")
def sources_bulk():
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    action = (request.form.get("action") or "").strip()
    if action not in {"enable", "disable", "always_surface_on", "always_surface_off"}:
        return render_template("errors/400.html", message="Invalid bulk action"), 400
    raw_ids = request.form.getlist("source_sel")
    decoded: list[tuple[str, str]] = []
    seen: set[str] = set()
    for token in raw_ids:
        if ":" not in token:
            return render_template("errors/400.html", message="Invalid source selection"), 400
        enc, rev = token.rsplit(":", 1)
        try:
            key = decode_opaque(enc, kind="source")
        except IdError:
            return render_template("errors/400.html", message="Invalid source id"), 400
        if key not in seen:
            seen.add(key)
            decoded.append((key, rev))
    if not decoded:
        return render_template("errors/400.html", message="No sources selected"), 400
    if len(decoded) > _BULK_MAX:
        return (
            render_template(
                "errors/400.html",
                message=f"Too many sources selected (max {_BULK_MAX})",
            ),
            400,
        )

    state_dir = Path(current_app.config["STATE_DIR"])
    try:
        lock = acquire_state_lock(
            state_dir, run_id=str(uuid.uuid4()), operation="web-source-bulk"
        )
    except RunLockError:
        return (
            render_template(
                "errors/503.html",
                message="Database busy (digest or another writer). Retry shortly.",
            ),
            503,
        )
    try:
        with mutation_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for key, expected in decoded:
                    try:
                        record = get_source_record(conn, key)
                    except SourceNotFound as exc:
                        conn.rollback()
                        return render_template("errors/400.html", message=str(exc)), 400
                    current = compute_source_revision(conn, record.source_key)
                    if current != expected:
                        conn.rollback()
                        return (
                            render_template(
                                "errors/409.html",
                                message="A selected source changed; reload and retry.",
                            ),
                            409,
                        )
                    if action == "enable":
                        updates = {"enabled": True}
                    elif action == "disable":
                        updates = {"enabled": False}
                    elif action == "always_surface_on":
                        updates = {"always_surface": True}
                    else:
                        updates = {"always_surface": False}
                    set_overrides(
                        conn,
                        record.source_key,
                        updates=updates,
                        updated_by="web",
                        commit=False,
                    )
                conn.commit()
            except SourceRegistryError as exc:
                conn.rollback()
                return render_template("errors/400.html", message=str(exc)), 400
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return (
                render_template(
                    "errors/503.html",
                    message="Database busy (digest or another writer). Retry shortly.",
                ),
                503,
            )
        raise
    finally:
        lock.release()
    return redirect(url_for("sources.list_registry"))


@bp.post("/sources/<id_enc>/alias/preview")
def source_alias_preview(id_enc: str):
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    try:
        canonical_raw = decode_opaque(id_enc, kind="source")
    except IdError:
        return render_template("errors/404.html", message="Invalid source id"), 404
    alias_raw = (request.form.get("alias_key") or "").strip()
    note = (request.form.get("note") or "").strip() or None
    if not alias_raw:
        return render_template("errors/400.html", message="alias_key required"), 400
    try:
        canonical = resolve_alias(g.db_ro, canonical_raw)
        alias_resolved = resolve_alias(g.db_ro, alias_raw)
        can_rec = get_source_record(g.db_ro, canonical)
        can_rev = compute_source_revision(g.db_ro, can_rec.source_key)
    except (SourceNotFound, SourceRegistryError) as exc:
        return render_template("errors/400.html", message=str(exc)), 400
    if alias_raw == canonical or alias_resolved == canonical:
        return render_template("errors/400.html", message="Cannot alias a source to itself"), 400
    alias_is_source = (
        g.db_ro.execute(
            "SELECT 1 FROM sources WHERE source_key = ?", (alias_raw,)
        ).fetchone()
        is not None
    )
    alias_rev = None
    if alias_is_source:
        try:
            alias_rev = compute_source_revision(g.db_ro, alias_raw)
        except SourceNotFound:
            alias_rev = None
    scope_fp = fingerprint_parts("alias", alias_raw, canonical)
    preview_fp = fingerprint_parts("alias", alias_raw, canonical, can_rev, alias_rev)
    token = issue_maintenance_token(
        secret=current_app.secret_key,
        action="alias",
        scope_fingerprint=scope_fp,
        preview_fingerprint=preview_fp,
    )
    aliases = g.db_ro.execute(
        "SELECT alias_key, note FROM source_aliases WHERE canonical_source_key = ? "
        "ORDER BY alias_key",
        (can_rec.source_key,),
    ).fetchall()
    scholar_alert, scholar_mode = _scholar_banner(can_rec)
    return render_template(
        "sources/detail.html",
        record=can_rec,
        revision=can_rev,
        aliases=aliases,
        id_enc=encode_opaque(can_rec.source_key),
        newsletter_types=sorted(NEWSLETTER_TYPES),
        grouping_policies=sorted(GROUPING_POLICIES),
        cadence_labels=sorted(CADENCE_LABELS),
        summary_profiles=sorted(_effective_summary_profiles()),
        scholar_alert=scholar_alert,
        scholar_mode=scholar_mode,
        alias_preview={
            "alias_key": alias_raw,
            "canonical_key": canonical,
            "note": note,
            "merge": alias_is_source,
            "token": token,
            "alias_revision": alias_rev,
            "canonical_revision": can_rev,
        },
    )


@bp.post("/sources/<id_enc>/alias/confirm")
def source_alias_confirm(id_enc: str):
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    try:
        canonical_raw = decode_opaque(id_enc, kind="source")
    except IdError:
        return render_template("errors/404.html", message="Invalid source id"), 404
    alias_raw = (request.form.get("alias_key") or "").strip()
    note = (request.form.get("note") or "").strip() or None
    token = request.form.get("confirm_token")
    if not alias_raw or not token:
        return render_template("errors/400.html", message="Missing alias confirmation"), 400

    state_dir = Path(current_app.config["STATE_DIR"])
    try:
        lock = acquire_state_lock(
            state_dir, run_id=str(uuid.uuid4()), operation="web-source-alias"
        )
    except RunLockError:
        return (
            render_template(
                "errors/503.html",
                message="Database busy (digest or another writer). Retry shortly.",
            ),
            503,
        )
    try:
        with mutation_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                canonical = resolve_alias(conn, canonical_raw)
                can_rev = compute_source_revision(conn, canonical)
                alias_is_source = (
                    conn.execute(
                        "SELECT 1 FROM sources WHERE source_key = ?", (alias_raw,)
                    ).fetchone()
                    is not None
                )
                alias_rev = (
                    compute_source_revision(conn, alias_raw) if alias_is_source else None
                )
                scope_fp = fingerprint_parts("alias", alias_raw, canonical)
                preview_fp = fingerprint_parts(
                    "alias", alias_raw, canonical, can_rev, alias_rev
                )
                ok, code = consume_maintenance_token(
                    token,
                    secret=current_app.secret_key,
                    action="alias",
                    scope_fingerprint=scope_fp,
                    preview_fingerprint=preview_fp,
                )
                if not ok:
                    conn.rollback()
                    status = 409 if code in {"stale_preview", "replay", "expired"} else 400
                    return (
                        render_template(
                            "errors/400.html",
                            message=f"Alias confirmation rejected ({code})",
                        ),
                        status,
                    )
                alias_sources(
                    conn,
                    alias_raw,
                    canonical,
                    note=note,
                    updated_by="web",
                    transaction="caller",
                )
                conn.commit()
            except SourceRegistryError as exc:
                conn.rollback()
                return render_template("errors/400.html", message=str(exc)), 400
            except SourceNotFound as exc:
                conn.rollback()
                return render_template("errors/404.html", message=str(exc)), 404
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return (
                render_template(
                    "errors/503.html",
                    message="Database busy (digest or another writer). Retry shortly.",
                ),
                503,
            )
        raise
    finally:
        lock.release()
    return redirect(url_for("sources.source_detail", id_enc=encode_opaque(canonical)))
