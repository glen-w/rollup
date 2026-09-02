"""Flask application factory for rollup web."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from flask import (
    Flask,
    abort,
    g,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from rollup.assets import LOGO_FILENAME, asset_bytes
from rollup.state import SchemaCompatibilityError, init_db
from rollup.web.csrf import init_csrf
from rollup.web.db import open_readonly
from rollup.web.headers import init_security_headers
from rollup.web.secrets import load_or_create_secret

_BRANDING = {
    LOGO_FILENAME: "image/png",
}


def refresh_config_derived(app: Flask) -> None:
    """Reload folder themes and [ui] prefs from the configured TOML path."""
    _refresh_config_derived(app)


def _refresh_config_derived(app: Flask) -> None:
    try:
        from rollup.web.config import load_web_config_document

        with app.app_context():
            doc = load_web_config_document()
        from rollup.linkedin.config import merge_linkedin_folder_themes

        app.config["FOLDER_THEMES"] = merge_linkedin_folder_themes(
            dict(doc.loaded.folder_themes), doc.loaded.linkedin
        )
        app.config["UI_LANDING_PAGE"] = doc.loaded.ui.landing_page
        app.config["UI_PREFERRED_VIEW"] = doc.loaded.ui.preferred_view
    except Exception:
        app.config.setdefault("FOLDER_THEMES", {})
        app.config.setdefault("UI_LANDING_PAGE", "archive")
        app.config.setdefault("UI_PREFERRED_VIEW", "html")


def create_app(
    *,
    state_dir: Path,
    output_dir: Path,
    mail_root: Path | None = None,
    newsletter_root: Path | None = None,
    testing: bool = False,
    config_path: str | Path | None = None,
) -> Flask:
    state_dir = Path(state_dir)
    output_dir = Path(output_dir)
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    secret = load_or_create_secret(state_dir)
    app.secret_key = secret
    db_path = state_dir / "rollup.db"
    app.config.update(
        STATE_DIR=state_dir,
        OUTPUT_DIR=output_dir,
        MAIL_ROOT=Path(mail_root) if mail_root else None,
        NEWSLETTER_ROOT=Path(newsletter_root) if newsletter_root else None,
        LOG_DIR=None,
        DB_PATH=db_path,
        WEB_BIND_HOST="127.0.0.1",
        WEB_BIND_PORT=8765,
        WEB_DEBUG=False,
        WEB_ENFORCE_HOST=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=False,
        SESSION_COOKIE_PATH="/",
        MAX_CONTENT_LENGTH=1_000_000,
        TESTING=testing,
        ADMIN_MANIFEST_MAX_DIR_ENTRIES=500,
        ADMIN_MANIFEST_MAX_FILES=50,
        ADMIN_MANIFEST_MAX_BYTES=512_000,
        ADMIN_BACKFILL_MAX_CANDIDATES=5000,
        CONFIG_PATH=str(Path(config_path).expanduser()) if config_path else None,
        CONFIG_EXPLICIT=bool(config_path),
        FOLDER_THEMES={},
        UI_LANDING_PAGE="archive",
        UI_PREFERRED_VIEW="html",
    )

    # Controlled startup schema initialisation — never in the per-request hook.
    init_db(db_path).close()

    _refresh_config_derived(app)

    init_csrf(app)
    init_security_headers(app)

    from rollup.web.format import (
        external_link_attrs,
        folder_accent_class,
        folder_display_name,
        folder_section_id,
        format_display_sender,
        format_human_date_range,
        format_human_datetime,
        format_newsletter_type,
    )

    app.jinja_env.filters["human_datetime"] = format_human_datetime
    app.jinja_env.filters["human_date_range"] = format_human_date_range
    app.jinja_env.filters["display_sender"] = format_display_sender
    app.jinja_env.filters["newsletter_type_label"] = format_newsletter_type
    app.jinja_env.globals["external_link_attrs"] = external_link_attrs
    app.jinja_env.globals["folder_display_name"] = folder_display_name
    app.jinja_env.globals["folder_accent_class"] = folder_accent_class
    app.jinja_env.globals["folder_section_id"] = folder_section_id

    @app.before_request
    def _open_db_ro() -> None:
        import sqlite3

        if request.endpoint == "static":
            return
        path = Path(app.config["DB_PATH"])
        try:
            g.db_ro = open_readonly(path)
        except FileNotFoundError:
            abort(503)
        except SchemaCompatibilityError as exc:
            g.db_ro = None
            g.db = None
            g.schema_error = str(exc)
            if request.method in ("GET", "HEAD"):
                return make_response(
                    render_template("errors/400.html", message=str(exc)),
                    400,
                )
            abort(400)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                abort(503)
            raise

        g.schema_error = None
        # Every request gets query-only only. Mutation routes open short-lived
        # write connections explicitly after CSRF/form validation.
        g.db = g.db_ro
        g.db_write = None

    @app.teardown_request
    def _close_db(exc: BaseException | None) -> None:
        for attr in ("db_write", "db_ro"):
            conn = getattr(g, attr, None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        g.db = None
        g.db_ro = None
        g.db_write = None

    @app.errorhandler(503)
    def _service_unavailable(err):
        from flask import make_response as _mr

        response = _mr(
            render_template(
                "errors/503.html",
                message="Database busy (digest or another writer). Retry shortly.",
            ),
            503,
        )
        response.headers["Retry-After"] = "5"
        return response

    @app.errorhandler(400)
    def _bad_request(err):
        message = getattr(err, "description", None) or "Bad request"
        return render_template("errors/400.html", message=message), 400

    from rollup.web.routes.admin import bp as admin_bp
    from rollup.web.routes.artifacts import bp as artifacts_bp
    from rollup.web.routes.messages import bp as messages_bp
    from rollup.web.routes.rollups import bp as rollups_bp
    from rollup.web.routes.sources import bp as sources_bp
    from rollup.web.routes.settings import bp as settings_bp
    from rollup.web.routes.run import bp as run_bp
    from rollup.web.routes.articles import bp as articles_bp
    from rollup.web.routes.reddit import bp as reddit_bp
    from rollup.web.routes.linkedin import bp as linkedin_bp

    app.register_blueprint(rollups_bp)
    app.register_blueprint(sources_bp)
    app.register_blueprint(messages_bp)
    app.register_blueprint(artifacts_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(run_bp)
    app.register_blueprint(articles_bp)
    app.register_blueprint(reddit_bp)
    app.register_blueprint(linkedin_bp)

    @app.get("/branding/<name>")
    def branding(name: str):
        mimetype = _BRANDING.get(name)
        if mimetype is None:
            abort(404)
        return send_file(
            BytesIO(asset_bytes(name)),
            mimetype=mimetype,
            download_name=name,
            max_age=0,
        )

    @app.get("/")
    def index():
        landing = app.config.get("UI_LANDING_PAGE") or "archive"
        if landing == "run":
            return redirect(url_for("run.run_studio"))
        if landing == "settings":
            return redirect(url_for("settings.settings_index"))
        return redirect(url_for("rollups.list_rollups"))

    return app
