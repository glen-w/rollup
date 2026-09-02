"""Reddit subreddit configuration routes."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from rollup.config_service import (
    ConfigConflictError,
    ConfigPatch,
    ConfigValidationError,
    apply_and_save,
    validate_patch,
)
from rollup.reddit.config import RedditConfig, RedditSub, normalize_sub_name
from rollup.reddit.cache import count_subs_needing_fetch
from rollup.reddit.config import filter_reddit_subs
from rollup.reddit.fetch import SUB_FETCH_BACKOFF_SECONDS, reddit_fetch_eta_phrase
from rollup.state import connect_db_mutator
from rollup.web.config import load_web_config_document
from rollup.web.csrf import validate_csrf_token as csrf_ok

bp = Blueprint("reddit", __name__, url_prefix="/reddit")

REDDIT_SORTS = ("hot", "new", "top", "rising", "controversial")
REDDIT_MODES = ("summary", "posts")
REDDIT_LAYOUTS = ("feed", "per_source")


def _normalize_sub_name(raw: str) -> str | None:
    return normalize_sub_name(raw)


def _reddit_from_form(base: RedditConfig) -> RedditConfig:
    enabled = "1" in request.form.getlist("reddit_enabled")
    layout = request.form.get("reddit_layout", base.layout)
    sort = request.form.get("reddit_sort", base.sort)
    mode = request.form.get("reddit_mode", base.mode)
    limit_raw = request.form.get("reddit_limit", str(base.limit)).strip()
    ttl_raw = request.form.get("reddit_fetch_ttl_hours", str(base.fetch_ttl_hours)).strip()
    try:
        limit = int(limit_raw)
    except ValueError:
        limit = base.limit
    limit = max(1, min(50, limit))
    try:
        fetch_ttl_hours = int(ttl_raw)
    except ValueError:
        fetch_ttl_hours = base.fetch_ttl_hours
    fetch_ttl_hours = max(0, min(168, fetch_ttl_hours))

    names = request.form.getlist("sub_name")
    mode_overrides = request.form.getlist("sub_mode")
    sort_overrides = request.form.getlist("sub_sort")
    limit_overrides = request.form.getlist("sub_limit")
    remove_names = {n.strip().lower() for n in request.form.getlist("sub_remove")}

    subs: dict[str, RedditSub] = {}
    for i, name_raw in enumerate(names):
        name = _normalize_sub_name(name_raw)
        if not name or name in remove_names:
            continue
        sub_enabled = request.form.get(f"sub_enabled_{name}") == "1"
        sub_mode = mode_overrides[i] if i < len(mode_overrides) else ""
        sub_sort = sort_overrides[i] if i < len(sort_overrides) else ""
        sub_limit_raw = limit_overrides[i].strip() if i < len(limit_overrides) else ""
        sub_limit = None
        if sub_limit_raw.isdigit():
            sub_limit = max(1, min(50, int(sub_limit_raw)))
        existing = base.subs.get(name)
        subs[name] = RedditSub(
            name=name,
            enabled=sub_enabled,
            mode=sub_mode if sub_mode in REDDIT_MODES else None,
            sort=sub_sort if sub_sort in REDDIT_SORTS else None,
            limit=sub_limit,
            display_name=existing.display_name if existing else None,
            emoji=existing.emoji if existing else None,
            accent=existing.accent if existing else None,
            order=existing.order if existing else None,
        )

    add_raw = request.form.get("add_sub", "").strip()
    add_name = _normalize_sub_name(add_raw) if add_raw else None
    if add_name and add_name not in subs and add_name not in remove_names:
        existing = base.subs.get(add_name)
        subs[add_name] = RedditSub(
            name=add_name,
            enabled=True,
            display_name=existing.display_name if existing else None,
            emoji=existing.emoji if existing else None,
            accent=existing.accent if existing else None,
            order=existing.order if existing else None,
        )

    if layout not in REDDIT_LAYOUTS:
        layout = base.layout
    if sort not in REDDIT_SORTS:
        sort = base.sort
    if mode not in REDDIT_MODES:
        mode = base.mode

    return RedditConfig(
        enabled=enabled,
        layout=layout,  # type: ignore[arg-type]
        sort=sort,  # type: ignore[arg-type]
        limit=limit,
        mode=mode,  # type: ignore[arg-type]
        time_filter=base.time_filter,
        fetch_ttl_hours=fetch_ttl_hours,
        subs=subs,
    )


@bp.get("")
def reddit_index():
    try:
        doc = load_web_config_document()
        reddit = doc.loaded.reddit
    except Exception:
        reddit = RedditConfig()
    subs_sorted = sorted(reddit.subs.values(), key=lambda s: s.name)
    enabled_subs = filter_reddit_subs(
        reddit,
        folders_include=(),
        folders_exclude=(),
    )
    enabled_sub_count = len(enabled_subs)
    fetch_count = enabled_sub_count
    try:
        from datetime import datetime

        from flask import current_app

        db_path = Path(current_app.config["DB_PATH"])
        if db_path.is_file():
            conn = connect_db_mutator(db_path)
            try:
                fetch_count = count_subs_needing_fetch(
                    conn,
                    enabled_subs,
                    config=reddit,
                    lookback_days=7,
                    ttl_hours=reddit.fetch_ttl_hours,
                    refresh=False,
                    now=datetime.now().astimezone(),
                )
            finally:
                conn.close()
    except Exception:
        fetch_count = enabled_sub_count
    return render_template(
        "reddit/index.html",
        reddit=reddit,
        subs=subs_sorted,
        sorts=REDDIT_SORTS,
        modes=REDDIT_MODES,
        layouts=REDDIT_LAYOUTS,
        enabled_sub_count=enabled_sub_count,
        reddit_fetch_eta=reddit_fetch_eta_phrase(fetch_count),
        reddit_backoff_seconds=int(SUB_FETCH_BACKOFF_SECONDS),
    )


@bp.post("/save")
def reddit_save():
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    try:
        doc = load_web_config_document()
    except Exception as exc:
        flash(str(exc))
        return redirect(url_for("reddit.reddit_index"))
    reddit = _reddit_from_form(doc.loaded.reddit)
    patch = ConfigPatch(reddit=reddit)
    issues = validate_patch(patch, base=doc.loaded)
    if any(i.severity == "error" for i in issues):
        flash(issues[0].message)
        return redirect(url_for("reddit.reddit_index"))
    try:
        apply_and_save(
            doc.path,
            patch,
            base_loaded=doc.loaded,
            expected_revision=doc.revision,
            backup_dir=Path(current_app.config["STATE_DIR"]) / "config-backups",
        )
    except ConfigConflictError:
        flash("Config changed elsewhere — reload and try again.")
        return redirect(url_for("reddit.reddit_index"))
    except ConfigValidationError as exc:
        flash(str(exc))
        return redirect(url_for("reddit.reddit_index"))
    flash("Reddit settings saved.")
    return redirect(url_for("reddit.reddit_index"))
