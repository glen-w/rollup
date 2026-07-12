"""Flask application factory for rollup web."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from flask import Flask, abort, g, redirect, render_template, send_file, url_for

from rollup.assets import LOGO_FILENAME, asset_bytes
from rollup.state import init_db
from rollup.web.csrf import init_csrf
from rollup.web.headers import init_security_headers
from rollup.web.secrets import load_or_create_secret

_BRANDING = {
    LOGO_FILENAME: "image/png",
}


def create_app(
    *,
    state_dir: Path,
    output_dir: Path,
    mail_root: Path | None = None,
    testing: bool = False,
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
    app.config.update(
        STATE_DIR=state_dir,
        OUTPUT_DIR=output_dir,
        MAIL_ROOT=Path(mail_root) if mail_root else None,
        DB_PATH=state_dir / "rollup.db",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_PATH="/",
        MAX_CONTENT_LENGTH=1_000_000,
        TESTING=testing,
    )

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
    def _open_db() -> None:
        import sqlite3

        try:
            g.db = init_db(app.config["DB_PATH"])
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                abort(503)
            raise

    @app.teardown_request
    def _close_db(exc: BaseException | None) -> None:
        conn = getattr(g, "db", None)
        if conn is not None:
            conn.close()

    @app.errorhandler(503)
    def _service_unavailable(err):
        return (
            render_template(
                "errors/503.html",
                message="Database busy (digest or another writer). Retry shortly.",
            ),
            503,
        )

    from rollup.web.routes.admin import bp as admin_bp
    from rollup.web.routes.artifacts import bp as artifacts_bp
    from rollup.web.routes.messages import bp as messages_bp
    from rollup.web.routes.rollups import bp as rollups_bp
    from rollup.web.routes.sources import bp as sources_bp

    app.register_blueprint(rollups_bp)
    app.register_blueprint(sources_bp)
    app.register_blueprint(messages_bp)
    app.register_blueprint(artifacts_bp)
    app.register_blueprint(admin_bp)

    @app.get("/branding/<name>")
    def branding(name: str):
        mimetype = _BRANDING.get(name)
        if mimetype is None:
            abort(404)
        return send_file(
            BytesIO(asset_bytes(name)),
            mimetype=mimetype,
            download_name=name,
            max_age=86_400,
        )

    @app.get("/")
    def index():
        return redirect(url_for("rollups.list_rollups"))

    return app
