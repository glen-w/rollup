"""Admin web routes for reader body diagnostics."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Blueprint, Response, current_app, g, render_template, request

from rollup.reader_body_admin import collect_stats, require_schema, run_check
from rollup.web.bind import is_loopback_host

bp = Blueprint("admin", __name__)


def _loopback_ok() -> bool:
    host = (request.host or "").split(":")[0]
    return is_loopback_host(host)


@bp.get("/admin")
def admin_index():
    db_path = Path(current_app.config["DB_PATH"])
    try:
        require_schema(g.db)
    except RuntimeError as exc:
        return render_template("errors/400.html", message=str(exc)), 400
    stats = collect_stats(g.db, db_path=db_path)
    report = run_check(g.db)
    resp = Response(
        render_template(
            "admin/index.html",
            stats=stats,
            report=report,
            loopback_warning=not _loopback_ok(),
        )
    )
    resp.headers["Cache-Control"] = "private, no-store"
    return resp
