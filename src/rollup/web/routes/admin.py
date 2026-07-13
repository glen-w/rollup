"""Admin web routes for reader body diagnostics."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Blueprint, Response, current_app, g, render_template, request

from rollup.payload_limits import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MAX_READER_BODY_LEN,
    MAX_READER_HTML_CHARS,
)
from rollup.reader_body_admin import collect_stats, require_schema, run_check
from rollup.web.bind import is_loopback_host

bp = Blueprint("admin", __name__)

SettingSection = tuple[str, list[tuple[str, str]]]


def _fmt_path(path: Path | None) -> str:
    if path is None:
        return "—"
    return str(path.expanduser().resolve())


def collect_web_settings() -> list[SettingSection]:
    """Read-only snapshot of paths, server bind, and web limits."""
    cfg = current_app.config
    state_dir = Path(cfg["STATE_DIR"])
    sections: list[SettingSection] = [
        (
            "Paths",
            [
                ("State directory", _fmt_path(state_dir)),
                ("Output directory", _fmt_path(Path(cfg["OUTPUT_DIR"]))),
                ("Mail root", _fmt_path(cfg.get("MAIL_ROOT"))),
                ("Log directory", _fmt_path(cfg.get("LOG_DIR"))),
                ("Database", _fmt_path(Path(cfg["DB_PATH"]))),
                ("Session secret", _fmt_path(state_dir / "web_secret")),
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
            settings=collect_web_settings(),
            loopback_warning=not _loopback_ok(),
        )
    )
    resp.headers["Cache-Control"] = "private, no-store"
    return resp
