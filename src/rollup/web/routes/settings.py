"""Configuration Centre — edit real digest TOML (not SQLite settings)."""

from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from rollup.config_service import (
    ConfigConflictError,
    ConfigPatch,
    ConfigValidationError,
    apply_and_save,
    effective_diff,
    patch_from_form_values,
    resolve_effective,
    validate_patch,
)
from rollup.discovery import list_flat_mbox_names, list_linkedin_folder_names
from rollup.web.config import load_web_config_document
from rollup.effort import (
    EFFORT_NAMES,
    EFFORT_PROFILE_SLOTS,
    EffortModelOverride,
    effort_editor_rows,
)
from rollup.folder_theme import FolderThemeOverride, folder_slug, theme_for
from rollup.linkedin.config import LinkedInConfig, LinkedInSearch
from rollup.output_writers import discover_writers
from rollup.run_profiles import list_run_profiles
from rollup.user_config import (
    UI_LANDING_PAGES,
    UI_PREFERRED_VIEWS,
    UiPreferences,
    UserConfigError,
)
from rollup.web.csrf import rotate_csrf_token, validate_csrf_token
from rollup.web.maintenance_tokens import (
    consume_maintenance_token,
    fingerprint_parts,
    issue_maintenance_token,
)

bp = Blueprint("settings", __name__, url_prefix="/settings")


def _load_doc():
    return load_web_config_document()


def _parse_csv(raw: str | None) -> list[str]:
    if not raw or not raw.strip():
        return []
    return [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]


def _folder_themes_from_form() -> dict[str, FolderThemeOverride]:
    themes: dict[str, FolderThemeOverride] = {}
    slugs = request.form.getlist("folder_slug")
    emojis = request.form.getlist("folder_emoji")
    accents = request.form.getlist("folder_accent")
    display_names = request.form.getlist("folder_display_name")
    orders = request.form.getlist("folder_order")
    for i, slug_raw in enumerate(slugs):
        slug = folder_slug(slug_raw.strip()) if slug_raw.strip() else ""
        if not slug:
            continue
        emoji = emojis[i].strip() if i < len(emojis) and emojis[i].strip() else None
        accent = accents[i].strip() if i < len(accents) and accents[i].strip() else None
        display = (
            display_names[i].strip()
            if i < len(display_names) and display_names[i].strip()
            else None
        )
        order_s = orders[i].strip() if i < len(orders) else ""
        order: int | None = None
        if order_s:
            try:
                order = int(order_s)
            except ValueError:
                order = None
        if any(v is not None for v in (emoji, accent, display, order)):
            themes[slug] = FolderThemeOverride(
                emoji=emoji,
                accent=accent,
                display_name=display,
                order=order,
            )
    return themes


def _linkedin_from_form() -> LinkedInConfig:
    enabled = "1" in request.form.getlist("linkedin_enabled")
    article_fetch = "1" in request.form.getlist("linkedin_article_fetch")
    searches: dict[str, LinkedInSearch] = {}
    slugs = request.form.getlist("linkedin_slug")
    urls = request.form.getlist("linkedin_url")
    display_names = request.form.getlist("linkedin_display_name")
    enabled_flags = request.form.getlist("linkedin_search_enabled")
    for i, slug_raw in enumerate(slugs):
        slug = slug_raw.strip().lower()
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
        search_enabled = (
            enabled_flags[i] == "1" if i < len(enabled_flags) else True
        )
        searches[slug] = LinkedInSearch(
            slug=slug,
            url=url,
            display_name=display,
            enabled=search_enabled,
        )
    return LinkedInConfig(enabled=enabled, article_fetch=article_fetch, searches=searches)


def _profiles_from_form() -> tuple[dict[str, dict], set[str]]:
    """Parse custom profile rows; return (profiles, remove set)."""
    profiles: dict[str, dict] = {}
    remove: set[str] = set()
    for name in request.form.getlist("remove_profile"):
        cleaned = name.strip()
        if cleaned and cleaned not in {"weekly", "daily"}:
            remove.add(cleaned)
    names = request.form.getlist("profile_name")
    lookbacks = request.form.getlist("profile_lookback")
    folders = request.form.getlist("profile_folder")
    efforts = request.form.getlist("profile_effort")
    ollama_flags = request.form.getlist("profile_ollama")
    for i, name_raw in enumerate(names):
        name = name_raw.strip()
        if not name or name in {"weekly", "daily"}:
            continue
        body: dict = {}
        if i < len(lookbacks) and lookbacks[i].strip():
            try:
                body["lookback_days"] = int(lookbacks[i].strip())
            except ValueError:
                pass
        if i < len(folders) and folders[i].strip():
            body["folder"] = _parse_csv(folders[i])
        if i < len(efforts) and efforts[i].strip():
            body["effort"] = efforts[i].strip()
        if i < len(ollama_flags) and ollama_flags[i] == "1":
            body["ollama"] = True
        elif i < len(ollama_flags) and ollama_flags[i] == "0":
            body["ollama"] = False
        profiles[name] = body
    return profiles, remove


def _effort_overrides_from_form() -> dict[str, EffortModelOverride] | None:
    """Parse effort model fields; None means the form omitted them (leave unchanged)."""
    if not any(key.startswith("effort_model_") for key in request.form):
        return None
    out: dict[str, EffortModelOverride] = {}
    for name in EFFORT_NAMES:
        profiles: dict[str, str] = {}
        for slot in EFFORT_PROFILE_SLOTS:
            raw = request.form.get(f"effort_model_{name}_{slot}", "").strip()
            if raw:
                profiles[slot] = raw
        ollama = request.form.get(f"effort_model_{name}_ollama_model", "").strip()
        review = request.form.get(
            f"effort_model_{name}_final_review_model", ""
        ).strip()
        override = EffortModelOverride(
            profiles=profiles,
            ollama_model=ollama or None,
            final_review_model=review or None,
        )
        if not override.is_empty():
            out[name] = override
    return out


def _effort_fingerprint(overrides: dict[str, EffortModelOverride] | None) -> str:
    if not overrides:
        return ""
    parts: list[str] = []
    for name in sorted(overrides):
        ov = overrides[name]
        slots = ",".join(f"{k}={v}" for k, v in sorted(ov.profiles.items()))
        parts.append(
            f"{name}:{slots}:{ov.ollama_model or ''}:{ov.final_review_model or ''}"
        )
    return "|".join(parts)


def _ui_from_form() -> UiPreferences:
    landing = request.form.get("landing_page", "archive").strip()
    preferred = request.form.get("preferred_view", "html").strip()
    complete = "1" in request.form.getlist("onboarding_complete")
    if landing not in UI_LANDING_PAGES:
        landing = "archive"
    if preferred not in UI_PREFERRED_VIEWS:
        preferred = "html"
    return UiPreferences(
        landing_page=landing,
        preferred_view=preferred,
        onboarding_complete=complete,
    )


def _patch_from_request() -> ConfigPatch:
    lookback_raw = request.form.get("lookback_days", "").strip()
    lookback = int(lookback_raw) if lookback_raw.isdigit() else None
    gmin_raw = request.form.get("grouping_min_size", "").strip()
    gmin = int(gmin_raw) if gmin_raw.isdigit() else None
    ollama_vals = request.form.getlist("ollama")
    ollama = "1" in ollama_vals if ollama_vals else None
    grouping_vals = request.form.getlist("no_grouping")
    no_grouping = "1" in grouping_vals if grouping_vals else None
    output_mode = request.form.get("output_mode", "all")
    selected = request.form.getlist("output_writer")
    if output_mode == "none":
        outputs = ["none"]
    elif output_mode == "all":
        outputs = ["all"]
    else:
        outputs = selected or ["none"]

    themes = _folder_themes_from_form()
    profiles, remove = _profiles_from_form()
    linkedin = _linkedin_from_form()
    return patch_from_form_values(
        mail_root=request.form.get("mail_root"),
        root=request.form.get("root"),
        output_dir=request.form.get("output_dir"),
        state_dir=request.form.get("state_dir"),
        log_dir=request.form.get("log_dir"),
        lookback_days=lookback,
        folder=_parse_csv(request.form.get("folder")),
        exclude_folder=_parse_csv(request.form.get("exclude_folder")),
        effort=request.form.get("effort") or None,
        ollama=ollama,
        ollama_model=request.form.get("ollama_model"),
        llm_provider=request.form.get("llm_provider") or None,
        llm_model=request.form.get("llm_model"),
        summary_profile=request.form.get("summary_profile") or None,
        no_grouping=no_grouping,
        grouping_min_size=gmin,
        profile=request.form.get("profile") or None,
        output=outputs,
        folder_themes=themes,
        profiles=profiles if profiles else None,
        remove_profiles=remove,
        ui=_ui_from_form(),
        effort_overrides=_effort_overrides_from_form(),
        linkedin=linkedin,
    )


def _refresh_app_paths(sticky: dict) -> list[str]:
    """Apply overlapping path keys to Flask config when safe."""
    notices: list[str] = []
    mapping = {
        "root": "NEWSLETTER_ROOT",
        "mail_root": "MAIL_ROOT",
        "output_dir": "OUTPUT_DIR",
        "log_dir": "LOG_DIR",
    }
    for key, flask_key in mapping.items():
        if key in sticky and sticky[key]:
            current_app.config[flask_key] = Path(str(sticky[key])).expanduser()
    if "state_dir" in sticky and sticky["state_dir"]:
        new_state = Path(str(sticky["state_dir"])).expanduser()
        old_state = Path(current_app.config["STATE_DIR"])
        if new_state.resolve() != old_state.resolve():
            notices.append(
                "state_dir changed — restart `rollup web` to open the new database."
            )
    return notices


def _onboarding_steps(doc, effective) -> list[tuple[str, bool, str]]:
    sticky = effective.sticky
    root_ok = bool(sticky.get("root")) and Path(
        str(sticky.get("root", ""))
    ).expanduser().exists()
    mail_ok = bool(sticky.get("mail_root"))
    paths_ok = root_ok and mail_ok
    folders_ok = bool(doc.loaded.folder_themes) or bool(sticky.get("folder"))
    summary_ok = "ollama" in sticky or doc.loaded.ui.onboarding_complete
    writers_ok = "output" in sticky or doc.loaded.ui.onboarding_complete
    return [
        ("Paths validated", paths_ok, "Set mail and newsletter roots under Paths."),
        (
            "Folders selected or themed",
            folders_ok,
            "Include folders or set presentation under Folder presentation.",
        ),
        (
            "Summary mode chosen",
            summary_ok,
            "Pick local preview or enable LLM summaries under Summaries.",
        ),
        (
            "Output writers chosen",
            writers_ok,
            "Choose Markdown/HTML-only or add-ons under Outputs.",
        ),
        (
            "Onboarding marked complete",
            doc.loaded.ui.onboarding_complete,
            "Check “Setup complete” under Personalisation after your first dry-run.",
        ),
    ]


@bp.get("")
@bp.get("/")
def settings_index():
    try:
        doc = _load_doc()
    except UserConfigError as exc:
        flash(f"Could not load config: {exc}")
        doc = None
    effective = resolve_effective(doc.loaded) if doc else None
    sticky = effective.sticky if effective else {}
    writers = []
    try:
        writers = sorted(discover_writers())
    except Exception:
        writers = ["xteink", "txt", "json", "epub"]
    newsletter = current_app.config.get("NEWSLETTER_ROOT")
    discovered = list_flat_mbox_names(newsletter)
    linkedin_names = list_linkedin_folder_names(
        doc.loaded.linkedin if doc else None,
        include=sticky.get("folder") or (),
        exclude=sticky.get("exclude_folder") or (),
    )
    discovered = discovered + [n for n in linkedin_names if n not in discovered]
    folder_rows = []
    themes = doc.loaded.folder_themes if doc else {}
    for name in discovered:
        slug = folder_slug(name)
        theme = theme_for(name, themes)
        override = themes.get(slug) or themes.get(name.lower())
        folder_rows.append(
            {
                "slug": slug,
                "name": name,
                "emoji": (override.emoji if override else None) or "",
                "accent": (override.accent if override else None) or theme.accent,
                "display_name": (override.display_name if override else None) or "",
                "order": override.order if override and override.order is not None else "",
                "preview_label": (
                    f"{(override.emoji + ' ') if override and override.emoji else ''}"
                    f"{(override.display_name if override and override.display_name else name)}"
                ),
            }
        )
    # Include theme-only slugs not discovered on disk
    known = {r["slug"] for r in folder_rows}
    for slug, override in themes.items():
        if slug not in known:
            folder_rows.append(
                {
                    "slug": slug,
                    "name": slug,
                    "emoji": override.emoji or "",
                    "accent": override.accent or "#4a7fd4",
                    "display_name": override.display_name or "",
                    "order": override.order if override.order is not None else "",
                    "preview_label": (
                        f"{(override.emoji + ' ') if override.emoji else ''}"
                        f"{override.display_name or slug}"
                    ),
                }
            )

    profiles = list_run_profiles(toml_profiles=doc.loaded.profiles if doc else {})
    sticky = effective.sticky if effective else {}
    onboarding = _onboarding_steps(doc, effective) if doc and effective else []
    return render_template(
        "settings/index.html",
        doc=doc,
        effective=effective,
        sticky=sticky,
        writers=writers,
        folder_rows=folder_rows,
        profiles=profiles,
        onboarding=onboarding,
        landing_pages=sorted(UI_LANDING_PAGES),
        preferred_views=sorted(UI_PREFERRED_VIEWS),
        effort_ladders=effort_editor_rows(doc.loaded.efforts if doc else {}),
        linkedin=doc.loaded.linkedin if doc else LinkedInConfig(),
        preview_token=None,
        diff_rows=None,
        patch_summary=None,
    )


@bp.post("/preview")
def settings_preview():
    if not validate_csrf_token(request.form.get("csrf_token")):
        flash("Invalid CSRF token")
        return redirect(url_for("settings.settings_index")), 400
    rotate_csrf_token()
    try:
        doc = _load_doc()
        patch = _patch_from_request()
        issues = validate_patch(patch, base=doc.loaded)
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            for err in errors:
                flash(f"{err.field}: {err.message}")
            return redirect(url_for("settings.settings_index"))
        before = resolve_effective(doc.loaded)
        # Build a temporary merged loaded view for after
        from rollup.user_config import LoadedUserConfig

        values = dict(doc.loaded.values)
        for k in patch.clear_values:
            values.pop(k, None)
        values.update(patch.values)
        after_loaded = LoadedUserConfig(
            values=values,
            folder_themes=(
                patch.folder_themes
                if patch.folder_themes is not None
                else doc.loaded.folder_themes
            ),
            profiles=(
                {**doc.loaded.profiles, **(patch.profiles or {})}
                if patch.profiles is not None or patch.remove_profiles
                else doc.loaded.profiles
            ),
            ui=patch.ui if patch.ui is not None else doc.loaded.ui,
            efforts=(
                patch.effort_overrides
                if patch.effort_overrides is not None
                else doc.loaded.efforts
            ),
            linkedin=(
                patch.linkedin if patch.linkedin is not None else doc.loaded.linkedin
            ),
            sources=doc.loaded.sources,
        )
        if patch.remove_profiles:
            profiles = dict(after_loaded.profiles)
            for name in patch.remove_profiles:
                profiles.pop(name, None)
            after_loaded = LoadedUserConfig(
                values=after_loaded.values,
                folder_themes=after_loaded.folder_themes,
                profiles=profiles,
                ui=after_loaded.ui,
                efforts=after_loaded.efforts,
                linkedin=after_loaded.linkedin,
                sources=after_loaded.sources,
            )
        after = resolve_effective(after_loaded)
        diff_rows = effective_diff(before, after)
        preview_fp = fingerprint_parts(
            doc.revision,
            str(sorted(patch.values.items())),
            str(sorted(patch.clear_values)),
            _effort_fingerprint(patch.effort_overrides),
        )
        token = issue_maintenance_token(
            secret=current_app.secret_key,
            action="settings_save",
            scope_fingerprint=fingerprint_parts(str(doc.path), doc.revision),
            preview_fingerprint=preview_fp,
        )
        # Stash patch fields in session via hidden form re-post on confirm —
        # we re-parse the same form fields on confirm.
        flash("Review the effective configuration diff, then confirm to save.")
        writers = sorted(discover_writers())
        return render_template(
            "settings/confirm.html",
            doc=doc,
            before=before,
            after=after,
            diff_rows=diff_rows,
            preview_token=token,
            preview_fp=preview_fp,
            form_data=request.form,
            writers=writers,
        )
    except UserConfigError as exc:
        flash(str(exc))
        return redirect(url_for("settings.settings_index"))


@bp.post("/save")
def settings_save():
    if not validate_csrf_token(request.form.get("csrf_token")):
        flash("Invalid CSRF token")
        return redirect(url_for("settings.settings_index")), 400
    rotate_csrf_token()
    try:
        doc = _load_doc()
        patch = _patch_from_request()
        preview_fp = request.form.get("preview_fp", "")
        ok, reason = consume_maintenance_token(
            request.form.get("confirm_token"),
            secret=current_app.secret_key,
            action="settings_save",
            scope_fingerprint=fingerprint_parts(str(doc.path), doc.revision),
            preview_fingerprint=preview_fp,
        )
        if not ok:
            flash(f"Save confirmation failed ({reason}). Preview again.")
            return redirect(url_for("settings.settings_index"))
        state_dir = Path(current_app.config["STATE_DIR"])
        saved = apply_and_save(
            doc.path,
            patch,
            expected_revision=doc.revision,
            backup_dir=state_dir / "config-backups",
        )
        notices = _refresh_app_paths(saved.loaded.values)
        from rollup.web.app import refresh_config_derived

        refresh_config_derived(current_app)
        flash(f"Saved configuration to {saved.path}")
        for n in notices:
            flash(n)
    except ConfigConflictError as exc:
        flash(str(exc))
        return redirect(url_for("settings.settings_index")), 409
    except ConfigValidationError as exc:
        flash(str(exc))
        return redirect(url_for("settings.settings_index")), 400
    except UserConfigError as exc:
        flash(str(exc))
        return redirect(url_for("settings.settings_index")), 400
    return redirect(url_for("settings.settings_index"))
