"""RSS feed configuration. Not an item inbox."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from rollup.config_service import (
    ConfigConflictError,
    ConfigPatch,
    ConfigValidationError,
    apply_and_save,
    validate_patch,
)
from rollup.rss.cache import get_feed_snapshot
from rollup.rss.config import RssConfig, RssFeed, parse_rss_config, slug_for_url
from rollup.source_fetch_cache import format_cache_age
from rollup.state import connect_db_readonly
from rollup.web.config import load_web_config_document
from rollup.web.csrf import validate_csrf_token as csrf_ok
from rollup.webpage.url import canonicalize_https_url

bp = Blueprint("rss", __name__, url_prefix="/rss")


def _rss_from_form(base: RssConfig) -> RssConfig:
    enabled = "1" in request.form.getlist("rss_enabled")
    layout = request.form.get("rss_layout", base.layout)
    if layout not in {"feed", "per_source"}:
        layout = "feed"
    ttl_raw = request.form.get("rss_fetch_ttl_hours", "").strip()
    ttl = int(ttl_raw) if ttl_raw.isdigit() else base.fetch_ttl_hours
    remove = set(request.form.getlist("rss_remove"))
    feeds: dict[str, RssFeed] = {}
    for slug, feed in base.feeds.items():
        if slug in remove:
            continue
        feed_enabled = "1" in request.form.getlist(f"rss_feed_enabled_{slug}")
        feeds[slug] = replace(feed, enabled=feed_enabled)
    add_url = (request.form.get("rss_add_url") or "").strip()
    if add_url:
        canonical = canonicalize_https_url(add_url)
        slug = slug_for_url(canonical)
        original = slug
        n = 2
        while slug in feeds and feeds[slug].url != canonical:
            slug = f"{original}-{n}"
            n += 1
        feeds[slug] = RssFeed(slug=slug, url=canonical, enabled=True)
    raw = {
        "enabled": enabled,
        "layout": layout,
        "fetch_ttl_hours": ttl,
        "feeds": {
            slug: {
                "url": feed.url,
                "enabled": feed.enabled,
                **({"display_name": feed.display_name} if feed.display_name else {}),
            }
            for slug, feed in feeds.items()
        },
    }
    return parse_rss_config(raw, path=Path("<rss-form>"))


@bp.get("")
def rss_index():
    try:
        doc = load_web_config_document()
        rss = doc.loaded.rss
    except Exception:
        rss = RssConfig()
    rows = []
    db_path = Path(current_app.config["DB_PATH"])
    now = datetime.now().astimezone()
    if db_path.is_file():
        conn = connect_db_readonly(db_path)
        try:
            for feed in sorted(rss.feeds.values(), key=lambda item: item.slug):
                snap = get_feed_snapshot(conn, feed.url)
                age = format_cache_age(snap.fetched_at, now) if snap else None
                rows.append({"feed": feed, "cache_age": age, "items": len(snap.item_keys) if snap else 0})
        finally:
            conn.close()
    else:
        rows = [{"feed": feed, "cache_age": None, "items": 0} for feed in rss.feeds.values()]
    return render_template(
        "rss/index.html",
        rss=rss,
        rows=rows,
        layouts=("feed", "per_source"),
    )


@bp.post("/save")
def rss_save():
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    try:
        doc = load_web_config_document()
    except Exception as exc:
        flash(str(exc), "error")
        return redirect(url_for("rss.rss_index"))
    try:
        rss = _rss_from_form(doc.loaded.rss)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("rss.rss_index"))
    patch = ConfigPatch(rss=rss)
    issues = validate_patch(patch, base=doc.loaded)
    if any(i.severity == "error" for i in issues):
        flash(issues[0].message, "error")
        return redirect(url_for("rss.rss_index"))
    try:
        apply_and_save(
            doc.path,
            patch,
            base_loaded=doc.loaded,
            expected_revision=doc.revision,
            backup_dir=Path(current_app.config["STATE_DIR"]) / "config-backups",
        )
    except ConfigConflictError:
        flash("Config changed elsewhere — reload and try again.", "warning")
        return redirect(url_for("rss.rss_index"))
    except ConfigValidationError as exc:
        flash(str(exc), "error")
        return redirect(url_for("rss.rss_index"))
    flash("RSS settings saved.", "success")
    return redirect(url_for("rss.rss_index"))
