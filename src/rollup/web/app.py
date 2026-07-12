"""Flask application factory for rollup web."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, abort, g, redirect, render_template, url_for

from rollup.state import init_db
from rollup.web.csrf import init_csrf
from rollup.web.headers import init_security_headers
from rollup.web.secrets import load_or_create_secret


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

    from rollup.web.routes.artifacts import bp as artifacts_bp
    from rollup.web.routes.messages import bp as messages_bp
    from rollup.web.routes.rollups import bp as rollups_bp
    from rollup.web.routes.sources import bp as sources_bp

    app.register_blueprint(rollups_bp)
    app.register_blueprint(sources_bp)
    app.register_blueprint(messages_bp)
    app.register_blueprint(artifacts_bp)

    @app.get("/")
    def index():
        return redirect(url_for("rollups.list_rollups"))

    return app
