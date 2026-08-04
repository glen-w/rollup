"""Admin web routes: read-only diagnostics + maintenance mutations."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from rollup.config import (
    DEFAULT_EFFORT,
    DEFAULT_LOOKBACK_DAYS,
)
from rollup.doctor_readonly import run_doctor_readonly
from rollup.payload_limits import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MAX_READER_BODY_LEN,
    MAX_READER_HTML_CHARS,
)
from rollup.reader_body_admin import (
    collect_stats,
    require_schema,
    run_check_cheap,
    run_check_deep,
)
from rollup.reader_body_backfill import (
    BackfillError,
    BackfillScope,
    delete_all_bodies,
    prune_orphans,
    scan_backfill_candidates,
    apply_backfill_writes,
    validate_newsletter_root,
)
from rollup.reader_body_store import bump_maintenance_generation, get_maintenance_generation
from rollup.run_lock import RunLockError, acquire_state_lock
from rollup.run_profiles import (
    DEFAULT_RUN_PROFILE,
    UnknownRunProfileError,
    list_run_profiles,
    resolve_run_profile,
)
from rollup.state import CANONICAL_TABLES, SCHEMA_VERSION, connect_db, get_schema_version
from rollup.user_config import load_user_config
from rollup.web.csrf import validate_csrf_token as csrf_ok
from rollup.web.db import mutation_connection, require_ro
from rollup.web.maintenance_tokens import (
    consume_maintenance_token,
    fingerprint_parts,
    invalidate_tokens_for_generation,
    issue_maintenance_token,
)
from rollup.web.manifest_health import ManifestScanLimits, collect_manifest_health

bp = Blueprint("admin", __name__)

SettingSection = tuple[str, list[tuple[str, str]]]


def _fmt_path(path: Path | None, *, home_relative: bool = True) -> str:
    if path is None:
        return "—"
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return str(path)
    if home_relative:
        home = Path.home().resolve()
        try:
            return "~/" + str(resolved.relative_to(home))
        except ValueError:
            pass
    return str(resolved)


def collect_web_settings() -> list[SettingSection]:
    """Read-only snapshot of web process paths/bind/limits (no secrets)."""
    cfg = current_app.config
    sections: list[SettingSection] = [
        (
            "Paths",
            [
                ("State directory", _fmt_path(Path(cfg["STATE_DIR"]))),
                ("Output directory", _fmt_path(Path(cfg["OUTPUT_DIR"]))),
                ("Mail root", _fmt_path(cfg.get("MAIL_ROOT"))),
                ("Newsletter root", _fmt_path(cfg.get("NEWSLETTER_ROOT"))),
                ("Log directory", _fmt_path(cfg.get("LOG_DIR"))),
                ("Database", _fmt_path(Path(cfg["DB_PATH"]))),
            ],
        ),
        (
            "Server",
            [
                ("Bind host", str(cfg.get("WEB_BIND_HOST", "—"))),
                ("Bind port", str(cfg.get("WEB_BIND_PORT", "—"))),
                ("Debug mode", "yes" if cfg.get("WEB_DEBUG") else "no"),
                ("Testing mode", "yes" if cfg.get("TESTING") else "no"),
            ],
        ),
        (
            "Limits",
            [
                (
                    "Max request body",
                    f"{int(cfg.get('MAX_CONTENT_LENGTH', 0)):,} bytes",
                ),
                ("Reader body cap", f"{MAX_READER_BODY_LEN:,} characters"),
                ("Reader HTML cap", f"{MAX_READER_HTML_CHARS:,} characters"),
                ("Default page size", str(DEFAULT_PAGE_SIZE)),
                ("Max page size", str(MAX_PAGE_SIZE)),
            ],
        ),
    ]
    return sections


def collect_digest_defaults() -> list[SettingSection]:
    """Pure resolver view of default digest effective configuration (not CLI print)."""
    cfg = current_app.config
    try:
        loaded = load_user_config()
    except Exception as exc:
        return [
            (
                "Default digest",
                [("Unavailable", f"{type(exc).__name__}: could not load user config")],
            )
        ]
    profile_name = loaded.values.get("profile") or DEFAULT_RUN_PROFILE
    try:
        profile = resolve_run_profile(profile_name, toml_profiles=loaded.profiles)
    except UnknownRunProfileError:
        profile_name = DEFAULT_RUN_PROFILE
        profile = resolve_run_profile(profile_name, toml_profiles=loaded.profiles)
    sticky: dict = {}
    sticky.update(loaded.values)
    sticky.pop("profile", None)
    sticky.update(profile.values)
    newsletter = cfg.get("NEWSLETTER_ROOT")
    mail = cfg.get("MAIL_ROOT")
    items = [
        ("Config sources", ", ".join(str(p) for p in loaded.sources) or "(none)"),
        ("Run profile", profile.name),
        ("Effort", str(sticky.get("effort") or DEFAULT_EFFORT)),
        ("Lookback days", str(sticky.get("lookback_days") or DEFAULT_LOOKBACK_DAYS)),
        ("Newsletter root (web)", _fmt_path(newsletter)),
        ("Mail root (web)", _fmt_path(mail)),
        ("Output directory (web)", _fmt_path(Path(cfg["OUTPUT_DIR"]))),
        ("State directory (web)", _fmt_path(Path(cfg["STATE_DIR"]))),
        (
            "Output kinds",
            ", ".join(sticky.get("output") or ["all"]),
        ),
        (
            "Folder include",
            ", ".join(sticky.get("folder") or []) or "(all)",
        ),
        (
            "Folder exclude",
            ", ".join(sticky.get("exclude_folder") or []) or "(none)",
        ),
        ("Ollama sticky", "yes" if sticky.get("ollama") else "no"),
        ("No grouping sticky", "yes" if sticky.get("no_grouping") else "no"),
        (
            "Profiles defined",
            ", ".join(
                sorted(p.name for p in list_run_profiles(toml_profiles=loaded.profiles))
            )
            or "(builtins only)",
        ),
    ]
    return [("Default digest effective configuration", items)]


def _panel(name: str, fn):
    try:
        return {"ok": True, "name": name, "data": fn(), "error": None}
    except Exception as exc:
        return {
            "ok": False,
            "name": name,
            "data": None,
            "error": f"{type(exc).__name__}: panel unavailable",
        }


def _build_admin_context(*, deep: bool = False) -> dict:
    db = require_ro()
    db_path = Path(current_app.config["DB_PATH"])
    state_dir = Path(current_app.config["STATE_DIR"])
    output_dir = Path(current_app.config["OUTPUT_DIR"])

    def bodies():
        require_schema(db)
        stats = collect_stats(db, db_path=db_path)
        report = run_check_deep(db) if deep else run_check_cheap(db)
        return {"stats": stats, "report": report, "deep": deep}

    def schema():
        ver = get_schema_version(db)
        existing = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        missing = sorted(CANONICAL_TABLES - existing)
        try:
            journal = db.execute("PRAGMA journal_mode").fetchone()[0]
        except sqlite3.Error:
            journal = "unknown"
        return {
            "schema_version": ver,
            "package_schema_version": SCHEMA_VERSION,
            "missing_tables": missing,
            "journal_mode": journal,
            "note": "journal_mode is reported read-only; Admin GET never changes it",
        }

    def doctor():
        return run_doctor_readonly(
            conn=db,
            state_dir=state_dir,
            output_dir=output_dir,
            mail_root=current_app.config.get("MAIL_ROOT"),
            newsletter_root=current_app.config.get("NEWSLETTER_ROOT"),
        )

    def manifests():
        limits = ManifestScanLimits(
            max_dir_entries=int(
                current_app.config.get("ADMIN_MANIFEST_MAX_DIR_ENTRIES", 500)
            ),
            max_files=int(current_app.config.get("ADMIN_MANIFEST_MAX_FILES", 50)),
            max_bytes=int(current_app.config.get("ADMIN_MANIFEST_MAX_BYTES", 512_000)),
        )
        if not deep:
            limits = ManifestScanLimits(
                max_dir_entries=min(limits.max_dir_entries, 100),
                max_files=min(limits.max_files, 15),
                max_bytes=min(limits.max_bytes, 256_000),
            )
        return collect_manifest_health(
            db, state_dir=state_dir, output_dir=output_dir, limits=limits
        )

    def settings():
        return collect_web_settings()

    def digest_defaults():
        return collect_digest_defaults()

    return {
        "panels": {
            "settings": _panel("settings", settings),
            "digest_defaults": _panel("digest_defaults", digest_defaults),
            "doctor": _panel("doctor", doctor),
            "schema": _panel("schema", schema),
            "manifests": _panel("manifests", manifests),
            "bodies": _panel("bodies", bodies),
        },
        "deep": deep,
        "prune_preview": None,
        "backfill_preview": None,
        "delete_preview": None,
        "vacuum_preview": None,
    }


def _mbox_fp(snapshots) -> str:
    parts: list[str] = []
    for path_s, snap in snapshots:
        if snap is None:
            parts.append(f"{path_s}:missing")
        else:
            parts.append(
                f"{path_s}:{snap.size}:{snap.mtime_ns}:{snap.st_dev}:{snap.st_ino}"
            )
    return fingerprint_parts(*sorted(parts))


def _newsletter_roots() -> tuple[Path, Path]:
    mail = current_app.config.get("MAIL_ROOT")
    news = current_app.config.get("NEWSLETTER_ROOT")
    if mail is None:
        raise BackfillError("MAIL_ROOT is not configured for this web process")
    if news is None:
        raise BackfillError(
            "NEWSLETTER_ROOT is not configured; refusing to scan mail_root"
        )
    return validate_newsletter_root(newsletter_root=Path(news), mail_root=Path(mail)), Path(
        mail
    )


@bp.get("/admin")
def admin_index():
    ctx = _build_admin_context(deep=False)
    resp = Response(render_template("admin/index.html", **ctx))
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.post("/admin/deep-check")
def admin_deep_check():
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    # Read-only: request hook opens only g.db_ro; no mutator.
    ctx = _build_admin_context(deep=True)
    resp = Response(render_template("admin/index.html", **ctx))
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


def _maintenance_confirm_flow(
    *,
    action: str,
    scope_fp: str,
    preview_fp: str,
    gen: int | None,
    prepare,
    mutate,
    success_message: str,
):
    """Lock → reconfirm preview → consume token → mutate."""
    state_dir = Path(current_app.config["STATE_DIR"])
    try:
        lock = acquire_state_lock(
            state_dir, run_id=str(uuid.uuid4()), operation=f"web-bodies-{action}"
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
            try:
                prepared = prepare(conn)
            except BackfillError as exc:
                return render_template("errors/409.html", message=str(exc)), 409
            ok, code = consume_maintenance_token(
                request.form.get("confirm_token"),
                secret=current_app.secret_key,
                action=action,
                scope_fingerprint=scope_fp,
                preview_fingerprint=preview_fp,
                maintenance_generation=gen,
            )
            if not ok:
                status = (
                    409
                    if code
                    in {"stale_preview", "replay", "expired", "stale_generation"}
                    else 400
                )
                return (
                    render_template(
                        "errors/400.html",
                        message=f"Maintenance confirmation rejected ({code})",
                    ),
                    status,
                )
            try:
                result = mutate(conn, prepared)
            except (BackfillError, RuntimeError, sqlite3.Error) as exc:
                if isinstance(exc, sqlite3.OperationalError) and (
                    "locked" in str(exc).lower() or "busy" in str(exc).lower()
                ):
                    return (
                        render_template(
                            "errors/503.html",
                            message="Database busy (digest or another writer). Retry shortly.",
                        ),
                        503,
                    )
                return render_template("errors/400.html", message=str(exc)), 400
            new_gen = get_maintenance_generation(conn)
            invalidate_tokens_for_generation(new_gen)
    finally:
        lock.release()
    flash(success_message.format(result=result))
    return redirect(url_for("admin.admin_index"))


@bp.post("/admin/bodies/prune")
def admin_bodies_prune():
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    db_ro = require_ro()
    try:
        require_schema(db_ro)
    except RuntimeError as exc:
        return render_template("errors/400.html", message=str(exc)), 400

    gen = get_maintenance_generation(db_ro)
    orphan_count = prune_orphans(db_ro, dry_run=True)
    scope_fp = fingerprint_parts("prune")
    preview_fp = fingerprint_parts("prune", gen, orphan_count)

    if request.form.get("confirm_token"):

        def prepare(conn):
            gen2 = get_maintenance_generation(conn)
            n2 = prune_orphans(conn, dry_run=True, commit=False)
            if fingerprint_parts("prune", gen2, n2) != preview_fp:
                raise BackfillError("Orphan set changed since preview; reload Admin.")
            return n2

        def mutate(conn, _prepared):
            return prune_orphans(conn, dry_run=False, commit=True)

        return _maintenance_confirm_flow(
            action="prune",
            scope_fp=scope_fp,
            preview_fp=preview_fp,
            gen=gen,
            prepare=prepare,
            mutate=mutate,
            success_message="Pruned {result} orphan reader body row(s).",
        )

    token = issue_maintenance_token(
        secret=current_app.secret_key,
        action="prune",
        scope_fingerprint=scope_fp,
        preview_fingerprint=preview_fp,
        maintenance_generation=gen,
    )
    ctx = _build_admin_context(deep=False)
    ctx["prune_preview"] = {"count": orphan_count, "token": token}
    resp = Response(render_template("admin/index.html", **ctx))
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.post("/admin/bodies/backfill")
def admin_bodies_backfill():
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    db_ro = require_ro()
    try:
        require_schema(db_ro)
        root, _mail = _newsletter_roots()
    except (RuntimeError, BackfillError) as exc:
        return render_template("errors/400.html", message=str(exc)), 400

    run_id = (request.form.get("run_id") or "").strip() or None
    scope = BackfillScope(retained_entries_only=True, run_id=run_id)
    max_c = int(current_app.config.get("ADMIN_BACKFILL_MAX_CANDIDATES", 5000))
    try:
        plan = scan_backfill_candidates(
            db_ro, newsletter_root=root, scope=scope, max_candidates=max_c
        )
    except BackfillError as exc:
        return render_template("errors/400.html", message=str(exc)), 400

    gen = get_maintenance_generation(db_ro)
    scope_fp = fingerprint_parts("backfill", run_id or "retained")
    preview_fp = fingerprint_parts(
        "backfill",
        gen,
        plan.candidates,
        len(plan.writes),
        plan.incomplete,
        _mbox_fp(plan.mbox_snapshots),
    )

    if request.form.get("confirm_token"):
        if plan.incomplete:
            return (
                render_template(
                    "errors/400.html",
                    message="Backfill scan incomplete (mbox changed); no write allowed.",
                ),
                400,
            )

        def prepare(conn):
            plan2 = scan_backfill_candidates(
                conn, newsletter_root=root, scope=scope, max_candidates=max_c
            )
            gen2 = get_maintenance_generation(conn)
            fp2 = fingerprint_parts(
                "backfill",
                gen2,
                plan2.candidates,
                len(plan2.writes),
                plan2.incomplete,
                _mbox_fp(plan2.mbox_snapshots),
            )
            if fp2 != preview_fp or plan2.incomplete:
                raise BackfillError("Backfill preview stale; reload Admin.")
            return plan2

        def mutate(conn, plan2):
            result = apply_backfill_writes(conn, plan2, commit=True)
            return result.matched

        return _maintenance_confirm_flow(
            action="backfill",
            scope_fp=scope_fp,
            preview_fp=preview_fp,
            gen=gen,
            prepare=prepare,
            mutate=mutate,
            success_message="Backfilled {result} reader body row(s).",
        )

    if plan.incomplete:
        ctx = _build_admin_context(deep=False)
        ctx["backfill_preview"] = {
            "error": "Scan incomplete: mbox changed during scan. Narrow scope and retry.",
            "candidates": plan.candidates,
            "matched": len(plan.writes),
            "ambiguous": len(plan.ambiguous_keys),
            "token": None,
        }
        resp = Response(render_template("admin/index.html", **ctx))
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    token = issue_maintenance_token(
        secret=current_app.secret_key,
        action="backfill",
        scope_fingerprint=scope_fp,
        preview_fingerprint=preview_fp,
        maintenance_generation=gen,
    )
    ctx = _build_admin_context(deep=False)
    ctx["backfill_preview"] = {
        "error": None,
        "candidates": plan.candidates,
        "matched": len(plan.writes),
        "ambiguous": len(plan.ambiguous_keys),
        "scanned": plan.scanned,
        "parse_failed": plan.parse_failed,
        "token": token,
        "run_id": run_id or "",
    }
    resp = Response(render_template("admin/index.html", **ctx))
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.post("/admin/bodies/delete-all")
def admin_bodies_delete_all():
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    db_ro = require_ro()
    try:
        require_schema(db_ro)
    except RuntimeError as exc:
        return render_template("errors/400.html", message=str(exc)), 400

    gen = get_maintenance_generation(db_ro)
    count = delete_all_bodies(db_ro, dry_run=True)
    scope_fp = fingerprint_parts("delete-all")
    preview_fp = fingerprint_parts("delete-all", gen, count)

    if request.form.get("confirm_token"):

        def prepare(conn):
            gen2 = get_maintenance_generation(conn)
            n2 = delete_all_bodies(conn, dry_run=True, commit=False)
            if fingerprint_parts("delete-all", gen2, n2) != preview_fp:
                raise BackfillError("Body set changed since preview; reload Admin.")
            return n2

        def mutate(conn, _prepared):
            return delete_all_bodies(conn, dry_run=False, commit=True)

        return _maintenance_confirm_flow(
            action="delete-all",
            scope_fp=scope_fp,
            preview_fp=preview_fp,
            gen=gen,
            prepare=prepare,
            mutate=mutate,
            success_message="Deleted {result} reader body row(s).",
        )

    token = issue_maintenance_token(
        secret=current_app.secret_key,
        action="delete-all",
        scope_fingerprint=scope_fp,
        preview_fingerprint=preview_fp,
        maintenance_generation=gen,
    )
    ctx = _build_admin_context(deep=False)
    ctx["delete_preview"] = {"count": count, "token": token}
    resp = Response(render_template("admin/index.html", **ctx))
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.post("/admin/bodies/vacuum")
def admin_bodies_vacuum():
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    db_ro = require_ro()
    try:
        require_schema(db_ro)
    except RuntimeError as exc:
        return render_template("errors/400.html", message=str(exc)), 400

    gen = get_maintenance_generation(db_ro)
    scope_fp = fingerprint_parts("vacuum")
    preview_fp = fingerprint_parts("vacuum", gen)

    if request.form.get("confirm_token"):
        state_dir = Path(current_app.config["STATE_DIR"])
        try:
            lock = acquire_state_lock(
                state_dir, run_id=str(uuid.uuid4()), operation="web-bodies-vacuum"
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
            # Reconfirm generation before consuming token.
            if get_maintenance_generation(require_ro()) != gen:
                return (
                    render_template(
                        "errors/409.html",
                        message="Maintenance generation changed; reload Admin.",
                    ),
                    409,
                )
            ok, code = consume_maintenance_token(
                request.form.get("confirm_token"),
                secret=current_app.secret_key,
                action="vacuum",
                scope_fingerprint=scope_fp,
                preview_fingerprint=preview_fp,
                maintenance_generation=gen,
            )
            if not ok:
                status = (
                    409
                    if code
                    in {"stale_preview", "replay", "expired", "stale_generation"}
                    else 400
                )
                return (
                    render_template(
                        "errors/400.html",
                        message=f"Maintenance confirmation rejected ({code})",
                    ),
                    status,
                )
            # VACUUM must run on a dedicated connection outside other txns.
            vac = connect_db(Path(current_app.config["DB_PATH"]))
            try:
                vac.execute("VACUUM")
            finally:
                vac.close()
            with mutation_connection() as conn:
                new_gen = bump_maintenance_generation(conn)
                conn.commit()
                invalidate_tokens_for_generation(new_gen)
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
        flash("Database vacuum completed.")
        return redirect(url_for("admin.admin_index"))

    token = issue_maintenance_token(
        secret=current_app.secret_key,
        action="vacuum",
        scope_fingerprint=scope_fp,
        preview_fingerprint=preview_fp,
        maintenance_generation=gen,
    )
    ctx = _build_admin_context(deep=False)
    ctx["vacuum_preview"] = {"token": token}
    resp = Response(render_template("admin/index.html", **ctx))
    resp.headers["Cache-Control"] = "private, no-store"
    return resp
