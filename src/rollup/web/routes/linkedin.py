"""LinkedIn named content-search configuration routes."""

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
from rollup.linkedin.config import (
    LINKEDIN_LAYOUTS,
    MAX_LINKEDIN_SEARCHES,
    LinkedInConfig,
    LinkedInSearch,
    search_slug_from_name,
)
from rollup.linkedin.url import LinkedInUrlError, from_member_ids, validate_content_search_url
from rollup.web.config import load_web_config_document
from rollup.web.csrf import validate_csrf_token as csrf_ok

bp = Blueprint("linkedin", __name__, url_prefix="/linkedin")

LINKEDIN_LAYOUT_CHOICES = ("feed", "per_source", "per_search")


def _linkedin_from_form(base: LinkedInConfig) -> LinkedInConfig:
    enabled = "1" in request.form.getlist("linkedin_enabled")
    article_fetch = "1" in request.form.getlist("linkedin_article_fetch")
    layout = request.form.get("linkedin_layout", base.layout)
    ttl_raw = request.form.get(
        "linkedin_fetch_ttl_hours", str(base.fetch_ttl_hours)
    ).strip()
    try:
        fetch_ttl_hours = int(ttl_raw)
    except ValueError:
        fetch_ttl_hours = base.fetch_ttl_hours
    fetch_ttl_hours = max(0, min(168, fetch_ttl_hours))
    if layout not in LINKEDIN_LAYOUTS:
        layout = base.layout

    slugs = request.form.getlist("search_slug")
    urls = request.form.getlist("search_url")
    display_names = request.form.getlist("search_display_name")
    originals = request.form.getlist("search_original_slug")
    remove = {s.strip().lower() for s in request.form.getlist("search_remove")}

    searches: dict[str, LinkedInSearch] = {}
    for i, slug_raw in enumerate(slugs):
        original = (
            originals[i].strip().lower() if i < len(originals) and originals[i].strip() else ""
        )
        if original and original in remove:
            continue
        slug = slug_raw.strip().lower() or original
        if not slug:
            continue
        url = urls[i].strip() if i < len(urls) else ""
        if not url:
            continue
        display = (
            display_names[i].strip()
            if i < len(display_names) and display_names[i].strip()
            else None
        )
        enabled_key = original or slug
        search_enabled = request.form.get(f"search_enabled_{enabled_key}") == "1"
        existing = base.searches.get(original) or base.searches.get(slug)
        if slug in searches:
            raise ValueError(f"Duplicate LinkedIn search slug: {slug}")
        validate_content_search_url(url, context=slug)
        searches[slug] = LinkedInSearch(
            slug=slug,
            url=url,
            display_name=display,
            enabled=search_enabled,
            emoji=existing.emoji if existing else None,
            accent=existing.accent if existing else None,
            order=existing.order if existing else None,
        )

    add_names = request.form.getlist("add_display_name")
    add_urls = request.form.getlist("add_url")
    add_slugs = request.form.getlist("add_slug")
    for i, url_raw in enumerate(add_urls):
        url = url_raw.strip()
        name = add_names[i].strip() if i < len(add_names) else ""
        slug_in = add_slugs[i].strip() if i < len(add_slugs) else ""
        if not url:
            if name or slug_in:
                raise ValueError(
                    "Paste a fromMember content-search URL to add a named search"
                )
            continue
        slug = (slug_in or search_slug_from_name(name)).lower()
        if not slug:
            raise ValueError("Give the new search a name (or an explicit slug)")
        if slug in searches or slug in remove:
            raise ValueError(f"A LinkedIn search named {slug!r} already exists")
        if len(searches) >= MAX_LINKEDIN_SEARCHES:
            raise ValueError(
                f"At most {MAX_LINKEDIN_SEARCHES} LinkedIn searches can be saved"
            )
        validate_content_search_url(url, context=slug)
        searches[slug] = LinkedInSearch(
            slug=slug,
            url=url,
            display_name=name or slug,
            enabled=True,
        )

    return LinkedInConfig(
        enabled=enabled,
        article_fetch=article_fetch,
        layout=layout,  # type: ignore[arg-type]
        fetch_ttl_hours=fetch_ttl_hours,
        searches=searches,
    )


@bp.get("")
def linkedin_index():
    try:
        doc = load_web_config_document()
        linkedin = doc.loaded.linkedin
    except Exception:
        linkedin = LinkedInConfig()
    searches_sorted = sorted(linkedin.searches.values(), key=lambda s: s.slug)
    rows = [
        {
            "search": search,
            "author_count": len(from_member_ids(search.url)),
        }
        for search in searches_sorted
    ]
    return render_template(
        "linkedin/index.html",
        linkedin=linkedin,
        rows=rows,
        layouts=LINKEDIN_LAYOUT_CHOICES,
    )


@bp.post("/save")
def linkedin_save():
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    try:
        doc = load_web_config_document()
    except Exception as exc:
        flash(str(exc))
        return redirect(url_for("linkedin.linkedin_index"))
    try:
        linkedin = _linkedin_from_form(doc.loaded.linkedin)
    except (LinkedInUrlError, ValueError) as exc:
        flash(str(exc))
        return redirect(url_for("linkedin.linkedin_index"))
    patch = ConfigPatch(linkedin=linkedin)
    issues = validate_patch(patch, base=doc.loaded)
    if any(i.severity == "error" for i in issues):
        flash(issues[0].message)
        return redirect(url_for("linkedin.linkedin_index"))
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
        return redirect(url_for("linkedin.linkedin_index"))
    except ConfigValidationError as exc:
        flash(str(exc))
        return redirect(url_for("linkedin.linkedin_index"))
    from rollup.web.app import refresh_config_derived

    refresh_config_derived(current_app)
    flash("LinkedIn searches saved.")
    return redirect(url_for("linkedin.linkedin_index"))
