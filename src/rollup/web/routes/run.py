"""Guided Run Studio — compose, preview, dry-run, and run digests from the web UI."""

from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from datetime import datetime, timezone

from rollup.config import DEFAULT_LOOKBACK_DAYS, DEFAULT_OLLAMA_URL, compute_date_window
from rollup.config_service import (
    build_digest_argv,
    resolve_effective,
)
from rollup.discovery import (
    list_flat_mbox_names,
    list_linkedin_folder_names,
    list_reddit_folder_names,
    list_webpage_folder_names,
)
from rollup.webpage.queue import count_in_window, count_items, count_pending
from rollup.llm_client import list_ollama_models
from rollup.run_profiles import list_run_profiles
from rollup.user_config import UserConfigError
from rollup.web.config import load_web_config_document
from rollup.web.csrf import rotate_csrf_token, validate_csrf_token
from rollup.web.run_progress import parse_run_progress
from rollup.web.run_runner import (
    get_active_run,
    is_busy,
    start_digest_subprocess,
)

bp = Blueprint("run", __name__, url_prefix="/run")


def _busy_response(message: str = "A digest is already running. Retry shortly."):
    flash(message)
    response = make_response(
        render_template("errors/503.html", message=message),
        503,
    )
    response.headers["Retry-After"] = "5"
    return response


def _config_path() -> Path:
    raw = current_app.config.get("CONFIG_PATH")
    if raw:
        return Path(raw)
    return load_web_config_document().path


def _load_effective(profile: str | None = None, overrides: dict | None = None):
    doc = load_web_config_document()
    return doc, resolve_effective(doc.loaded, profile_name=profile, overrides=overrides)


def _overrides_from_form() -> tuple[str | None, dict, list[str]]:
    profile = request.form.get("profile") or request.args.get("profile") or None
    overrides: dict = {}
    extra: list[str] = []
    lookback = request.form.get("lookback_days", "").strip()
    if lookback.isdigit():
        overrides["lookback_days"] = int(lookback)
    folder = request.form.get("folder", "").strip()
    if folder:
        overrides["folder"] = [p.strip() for p in folder.split(",") if p.strip()]
    effort = request.form.get("effort", "").strip()
    if effort:
        overrides["effort"] = effort
    if request.form.get("ollama") == "1":
        overrides["ollama"] = True
    elif request.form.get("ollama") == "0":
        overrides["ollama"] = False
    output_mode = request.form.get("output_mode", "")
    if output_mode == "none":
        overrides["output"] = ["none"]
    elif output_mode == "subset":
        selected = request.form.getlist("output_writer")
        overrides["output"] = selected or ["none"]
    elif output_mode == "all":
        overrides["output"] = ["all"]
    use_single = request.form.get("use_single_model") == "1"
    single_model = (request.form.get("single_model") or "").strip()
    if use_single and single_model:
        extra.extend(["--single-model", single_model, "--ollama"])
        overrides["ollama"] = True
    return profile, overrides, extra


def _single_model_context() -> dict[str, object]:
    return {
        "use_single_model": request.form.get("use_single_model") == "1",
        "selected_single_model": (request.form.get("single_model") or "").strip(),
    }


def _with_litellm_model(extra: list[str], sticky: dict) -> list[str]:
    if (
        extra
        and "--single-model" in extra
        and (sticky.get("llm_provider") or "ollama") == "litellm"
    ):
        idx = extra.index("--single-model")
        model = extra[idx + 1] if idx + 1 < len(extra) else ""
        if model:
            extra = list(extra) + ["--llm-model", model]
    return extra


def _webpage_counts(lookback_days: int | None = None) -> tuple[int, int, int]:
    """Return (pending, saved, in_window)."""
    from rollup.web.db import require_ro

    try:
        conn = require_ro()
        pending = count_pending(conn)
        saved = count_items(conn)
        days = lookback_days or DEFAULT_LOOKBACK_DAYS
        start, end = compute_date_window(datetime.now(timezone.utc), int(days))
        in_window = count_in_window(conn, window_start=start, window_end=end)
        return pending, saved, in_window
    except Exception:
        return 0, 0, 0


def _matched_folders(sticky: dict, linkedin_config=None, reddit_config=None) -> list[str]:
    root = sticky.get("root") or current_app.config.get("NEWSLETTER_ROOT")
    include = sticky.get("folder") or ()
    exclude = sticky.get("exclude_folder") or ()
    matched: list[str] = []
    if root:
        path = Path(str(root)).expanduser()
        matched = list_flat_mbox_names(path, include=include, exclude=exclude)
    linkedin = list_linkedin_folder_names(
        linkedin_config, include=include, exclude=exclude
    )
    for name in linkedin:
        if name not in matched:
            matched.append(name)
    pending, saved, _in_window = _webpage_counts()
    webpage = list_webpage_folder_names(
        item_count=saved or pending,
        include=include,
        exclude=exclude,
    )
    for name in webpage:
        if name not in matched:
            matched.append(name)
    reddit = list_reddit_folder_names(
        reddit_config, include=include, exclude=exclude
    )
    for name in reddit:
        if name not in matched:
            matched.append(name)
    return matched


def _reddit_sub_count(reddit_config, sticky: dict) -> int:
    from rollup.reddit.config import filter_reddit_subs

    return len(
        filter_reddit_subs(
            reddit_config,
            folders_include=tuple(sticky.get("folder") or ()),
            folders_exclude=tuple(sticky.get("exclude_folder") or ()),
        )
    )


@bp.get("")
@bp.get("/")
def run_studio():
    try:
        profile = request.args.get("profile")
        doc, effective = _load_effective(profile)
    except UserConfigError as exc:
        flash(str(exc))
        return render_template(
            "run/index.html",
            doc=None,
            effective=None,
            profiles=[],
            matched_folders=[],
            linkedin_enabled=False,
            linkedin_search_count=0,
            reddit_enabled=False,
            reddit_sub_count=0,
            webpage_pending_count=0,
            webpage_in_window_count=0,
            cli_command="",
            cron_hint="",
            active=get_active_run(),
            busy=is_busy(),
            use_single_model=False,
            selected_single_model="",
        )
    profiles = list_run_profiles(toml_profiles=doc.loaded.profiles)
    matched = _matched_folders(
        effective.sticky, doc.loaded.linkedin, doc.loaded.reddit
    )
    linkedin_searches = list_linkedin_folder_names(
        doc.loaded.linkedin,
        include=effective.sticky.get("folder") or (),
        exclude=effective.sticky.get("exclude_folder") or (),
    )
    reddit_sub_count = _reddit_sub_count(doc.loaded.reddit, effective.sticky)
    argv = build_digest_argv(effective, config_path=_config_path(), dry_run=False)
    cli = "rollup " + " ".join(_shell_quote(a) for a in argv)
    cron = f"0 7 * * 1 cd ~ && {cli} --cron"
    lookback = effective.sticky.get("lookback_days") or DEFAULT_LOOKBACK_DAYS
    webpage_pending, _saved, webpage_in_window = _webpage_counts(lookback)
    return render_template(
        "run/index.html",
        doc=doc,
        effective=effective,
        sticky=effective.sticky,
        profiles=profiles,
        matched_folders=matched,
        linkedin_enabled=doc.loaded.linkedin.enabled,
        linkedin_search_count=len(linkedin_searches),
        reddit_enabled=doc.loaded.reddit.enabled,
        reddit_sub_count=reddit_sub_count,
        webpage_pending_count=webpage_pending,
        webpage_in_window_count=webpage_in_window,
        cli_command=cli,
        cron_hint=cron,
        active=get_active_run(),
        busy=is_busy(),
        use_single_model=False,
        selected_single_model="",
    )


def _shell_quote(arg: str) -> str:
    if not arg or any(c in arg for c in ' \t\n"\'$`'):
        return "'" + arg.replace("'", "'\\''") + "'"
    return arg


@bp.post("/preview")
def run_preview():
    """Update the compose form and show effective run (GET redirect with args)."""
    if not validate_csrf_token(request.form.get("csrf_token")):
        flash("Invalid CSRF token")
        return redirect(url_for("run.run_studio")), 400
    rotate_csrf_token()
    profile, overrides, extra = _overrides_from_form()
    try:
        doc, effective = _load_effective(profile, overrides)
    except UserConfigError as exc:
        flash(str(exc))
        return redirect(url_for("run.run_studio"))
    extra = _with_litellm_model(extra, effective.sticky)
    matched = _matched_folders(
        effective.sticky, doc.loaded.linkedin, doc.loaded.reddit
    )
    linkedin_searches = list_linkedin_folder_names(
        doc.loaded.linkedin,
        include=effective.sticky.get("folder") or (),
        exclude=effective.sticky.get("exclude_folder") or (),
    )
    reddit_sub_count = _reddit_sub_count(doc.loaded.reddit, effective.sticky)
    argv = build_digest_argv(
        effective, config_path=_config_path(), dry_run=False, extra=extra or None
    )
    cli = "rollup " + " ".join(_shell_quote(a) for a in argv)
    cron = f"0 7 * * 1 cd ~ && {cli} --cron"
    profiles = list_run_profiles(toml_profiles=doc.loaded.profiles)
    lookback = effective.sticky.get("lookback_days") or DEFAULT_LOOKBACK_DAYS
    webpage_pending, _saved, webpage_in_window = _webpage_counts(lookback)
    flash("Effective run updated (not saved to TOML unless you use Settings).")
    return render_template(
        "run/index.html",
        doc=doc,
        effective=effective,
        sticky=effective.sticky,
        profiles=profiles,
        matched_folders=matched,
        linkedin_enabled=doc.loaded.linkedin.enabled,
        linkedin_search_count=len(linkedin_searches),
        reddit_enabled=doc.loaded.reddit.enabled,
        reddit_sub_count=reddit_sub_count,
        webpage_pending_count=webpage_pending,
        webpage_in_window_count=webpage_in_window,
        cli_command=cli,
        cron_hint=cron,
        active=get_active_run(),
        busy=is_busy(),
        temp_overrides=overrides,
        **_single_model_context(),
    )


@bp.post("/dry-run")
def run_dry():
    if not validate_csrf_token(request.form.get("csrf_token")):
        flash("Invalid CSRF token")
        return redirect(url_for("run.run_studio")), 400
    rotate_csrf_token()
    if is_busy():
        return _busy_response()
    profile, overrides, extra = _overrides_from_form()
    try:
        _doc, effective = _load_effective(profile, overrides)
        extra = _with_litellm_model(extra, effective.sticky)
        if (
            request.form.get("use_single_model") == "1"
            and not (request.form.get("single_model") or "").strip()
        ):
            flash('Choose a model, or uncheck “Use a single model for this run”.')
            return redirect(url_for("run.run_studio"))
        argv = build_digest_argv(
            effective, config_path=_config_path(), dry_run=True, extra=extra or None
        )
        start_digest_subprocess(argv, dry_run=True)
    except RuntimeError as exc:
        return _busy_response(str(exc))
    except UserConfigError as exc:
        flash(str(exc))
        return redirect(url_for("run.run_studio"))
    flash("Dry-run started — progress updates below.")
    return redirect(url_for("run.run_result"))


@bp.post("/start")
def run_start():
    if not validate_csrf_token(request.form.get("csrf_token")):
        flash("Invalid CSRF token")
        return redirect(url_for("run.run_studio")), 400
    rotate_csrf_token()
    if is_busy():
        return _busy_response()
    profile, overrides, extra = _overrides_from_form()
    try:
        _doc, effective = _load_effective(profile, overrides)
        extra = _with_litellm_model(extra, effective.sticky)
        if (
            request.form.get("use_single_model") == "1"
            and not (request.form.get("single_model") or "").strip()
        ):
            flash('Choose a model, or uncheck “Use a single model for this run”.')
            return redirect(url_for("run.run_studio"))
        argv = build_digest_argv(
            effective, config_path=_config_path(), dry_run=False, extra=extra or None
        )
        start_digest_subprocess(argv, dry_run=False)
    except RuntimeError as exc:
        return _busy_response(str(exc))
    except UserConfigError as exc:
        flash(str(exc))
        return redirect(url_for("run.run_studio"))
    flash("Digest started — progress updates below.")
    return redirect(url_for("run.run_result"))


@bp.post("/ollama-models")
def run_ollama_models():
    """List local Ollama tags. POST-only so GET /run never contacts Ollama."""
    token = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
    if not validate_csrf_token(token):
        return jsonify({"ok": False, "models": [], "error": "csrf"}), 400
    models = list_ollama_models(DEFAULT_OLLAMA_URL, timeout=2.0)
    return jsonify({"ok": True, "models": models})


def _status_payload(run) -> dict:
    import time

    log = list(run.log_lines)[-80:]
    progress = parse_run_progress(log, dry_run=run.dry_run, status=run.status)
    return {
        "run_id": run.run_id,
        "status": run.status,
        "exit_code": run.exit_code,
        "dry_run": run.dry_run,
        "log": log,
        "argv": run.argv,
        "error": run.error,
        "elapsed_seconds": max(0.0, time.time() - run.started_at),
        "progress": progress,
    }


@bp.get("/status")
def run_status():
    run = get_active_run()
    if run is None:
        return jsonify({"status": "idle"})
    return jsonify(_status_payload(run))


@bp.get("/result")
def run_result():
    run = get_active_run()
    # Newest archive link when available
    latest_run_id = None
    try:
        from rollup.web.db import open_readonly

        db = open_readonly(Path(current_app.config["DB_PATH"]))
        try:
            row = db.execute(
                "SELECT run_id FROM rollup_runs ORDER BY completed_at DESC LIMIT 1"
            ).fetchone()
            if row:
                latest_run_id = row[0]
        finally:
            db.close()
    except Exception:
        latest_run_id = None
    return render_template(
        "run/result.html",
        active=run,
        latest_run_id=latest_run_id,
    )
