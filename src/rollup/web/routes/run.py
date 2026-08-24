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

from rollup.config import DEFAULT_OLLAMA_URL
from rollup.config_service import (
    build_digest_argv,
    load_document,
    resolve_effective,
)
from rollup.llm_client import list_ollama_models
from rollup.run_profiles import list_run_profiles
from rollup.user_config import UserConfigError
from rollup.web.csrf import rotate_csrf_token, validate_csrf_token
from rollup.web.run_runner import (
    get_active_run,
    is_busy,
    start_digest_subprocess,
    wait_until_idle,
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
    return load_document().path


def _load_effective(profile: str | None = None, overrides: dict | None = None):
    explicit = current_app.config.get("CONFIG_PATH")
    doc = load_document(explicit=explicit) if explicit else load_document()
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


def _matched_folders(sticky: dict) -> list[str]:
    root = sticky.get("root") or current_app.config.get("NEWSLETTER_ROOT")
    if not root:
        return []
    path = Path(str(root)).expanduser()
    if not path.is_dir():
        return []
    include = set(sticky.get("folder") or [])
    exclude = set(sticky.get("exclude_folder") or [])
    names: list[str] = []
    try:
        for child in sorted(path.iterdir()):
            if not child.is_file() or child.name.startswith("."):
                continue
            if child.suffix in {".msf", ".dat", ".toc"}:
                continue
            if include and child.name not in include and child.name.lower() not in {
                f.lower() for f in include
            }:
                continue
            if child.name in exclude or child.name.lower() in {
                f.lower() for f in exclude
            }:
                continue
            names.append(child.name)
    except OSError:
        return []
    return names


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
            cli_command="",
            cron_hint="",
            active=get_active_run(),
            busy=is_busy(),
            use_single_model=False,
            selected_single_model="",
        )
    profiles = list_run_profiles(toml_profiles=doc.loaded.profiles)
    matched = _matched_folders(effective.sticky)
    argv = build_digest_argv(effective, config_path=_config_path(), dry_run=False)
    cli = "rollup " + " ".join(_shell_quote(a) for a in argv)
    cron = f"0 7 * * 1 cd ~ && {cli} --cron"
    return render_template(
        "run/index.html",
        doc=doc,
        effective=effective,
        sticky=effective.sticky,
        profiles=profiles,
        matched_folders=matched,
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
    matched = _matched_folders(effective.sticky)
    argv = build_digest_argv(
        effective, config_path=_config_path(), dry_run=False, extra=extra or None
    )
    cli = "rollup " + " ".join(_shell_quote(a) for a in argv)
    cron = f"0 7 * * 1 cd ~ && {cli} --cron"
    profiles = list_run_profiles(toml_profiles=doc.loaded.profiles)
    flash("Effective run updated (not saved to TOML unless you use Settings).")
    return render_template(
        "run/index.html",
        doc=doc,
        effective=effective,
        sticky=effective.sticky,
        profiles=profiles,
        matched_folders=matched,
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
        result = wait_until_idle(timeout=600)
    except RuntimeError as exc:
        return _busy_response(str(exc))
    except UserConfigError as exc:
        flash(str(exc))
        return redirect(url_for("run.run_studio"))
    if result is None:
        flash("Dry-run timed out.")
    else:
        flash(
            f"Dry-run finished: status={result.status}, exit={result.exit_code}"
        )
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
        result = wait_until_idle(timeout=3600)
    except RuntimeError as exc:
        return _busy_response(str(exc))
    except UserConfigError as exc:
        flash(str(exc))
        return redirect(url_for("run.run_studio"))
    if result is None:
        flash("Digest timed out.")
    else:
        flash(f"Digest finished: status={result.status}, exit={result.exit_code}")
    return redirect(url_for("run.run_result"))


@bp.post("/ollama-models")
def run_ollama_models():
    """List local Ollama tags. POST-only so GET /run never contacts Ollama."""
    token = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
    if not validate_csrf_token(token):
        return jsonify({"ok": False, "models": [], "error": "csrf"}), 400
    models = list_ollama_models(DEFAULT_OLLAMA_URL, timeout=2.0)
    return jsonify({"ok": True, "models": models})


@bp.get("/status")
def run_status():
    run = get_active_run()
    if run is None:
        return jsonify({"status": "idle"})
    return jsonify(
        {
            "run_id": run.run_id,
            "status": run.status,
            "exit_code": run.exit_code,
            "dry_run": run.dry_run,
            "log": list(run.log_lines)[-80:],
            "argv": run.argv,
            "error": run.error,
        }
    )


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
