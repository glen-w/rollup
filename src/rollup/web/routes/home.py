"""Home dashboard: one read-only, offline glance at the local briefing state."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Blueprint, current_app, g, render_template

from rollup.web.run_runner import get_active_run, is_busy

bp = Blueprint("home", __name__)


def _latest_run(conn: sqlite3.Connection) -> dict | None:
    cur = conn.execute(
        """SELECT run_id, started_at, status, window_start, window_end,
                  messages_included, sources_included, groups_created, degraded,
                  entry_index_version, markdown_relpath, html_relpath, manifest_relpath,
                  summaries_ollama, summaries_cache
           FROM rollup_runs
           ORDER BY started_at DESC, run_id DESC
           LIMIT 1"""
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "run_id": row[0],
        "started_at": row[1],
        "status": row[2],
        "window_start": row[3],
        "window_end": row[4],
        "messages_included": row[5],
        "sources_included": row[6],
        "groups_created": row[7],
        "degraded": bool(row[8]),
        "entry_index_version": row[9],
        "has_md": bool(row[10]),
        "has_html": bool(row[11]),
        "has_manifest": bool(row[12]),
        "summaries_ollama": row[13],
        "summaries_cache": row[14],
    }


def _counts(conn: sqlite3.Connection) -> dict:
    def scalar(sql: str) -> int | None:
        try:
            return int(conn.execute(sql).fetchone()[0])
        except (sqlite3.Error, TypeError):
            return None

    return {
        "runs": scalar("SELECT COUNT(*) FROM rollup_runs"),
        "sources": scalar("SELECT COUNT(*) FROM source_observations"),
        "sources_disabled": scalar(
            "SELECT COUNT(*) FROM source_overrides WHERE enabled = 0"
        ),
        "articles_pending": scalar(
            "SELECT COUNT(*) FROM webpage_queue WHERE status = 'pending'"
        ),
        "articles_total": scalar("SELECT COUNT(*) FROM webpage_queue"),
        "bodies": scalar("SELECT COUNT(*) FROM message_reader_bodies"),
    }


def _config_summary() -> dict:
    """Cheap, offline summary of the sticky config for the dashboard."""
    out: dict = {
        "ok": False,
        "profile": None,
        "lookback_days": None,
        "llm": False,
        "provider": None,
        "linkedin_enabled": False,
        "linkedin_searches": 0,
        "reddit_enabled": False,
        "reddit_subs": 0,
        "gmail_enabled": False,
        "onboarding": [],
        "onboarding_complete": True,
        "config_path": None,
    }
    try:
        from rollup.config_service import resolve_effective
        from rollup.web.config import load_web_config_document
        from rollup.web.routes.settings import _onboarding_steps

        doc = load_web_config_document()
        effective = resolve_effective(doc.loaded)
        sticky = effective.sticky
        out.update(
            ok=True,
            profile=effective.profile_name,
            lookback_days=sticky.get("lookback_days"),
            llm=bool(sticky.get("ollama")),
            provider=sticky.get("llm_provider") or "ollama",
            linkedin_enabled=doc.loaded.linkedin.enabled,
            linkedin_searches=len(
                [s for s in doc.loaded.linkedin.searches.values() if s.enabled]
            )
            if getattr(doc.loaded.linkedin, "searches", None)
            else 0,
            reddit_enabled=doc.loaded.reddit.enabled,
            reddit_subs=len(
                [s for s in doc.loaded.reddit.subs.values() if s.enabled]
            )
            if getattr(doc.loaded.reddit, "subs", None)
            else 0,
            gmail_enabled=doc.loaded.gmail.enabled,
            onboarding_complete=doc.loaded.ui.onboarding_complete,
            config_path=str(doc.path),
        )
        if not doc.loaded.ui.onboarding_complete:
            out["onboarding"] = _onboarding_steps(doc, effective)
    except Exception:
        pass
    return out


def _doctor_summary(conn: sqlite3.Connection) -> dict | None:
    try:
        from rollup.doctor_readonly import run_doctor_readonly

        report = run_doctor_readonly(
            conn=conn,
            state_dir=Path(current_app.config["STATE_DIR"]),
            output_dir=Path(current_app.config["OUTPUT_DIR"]),
            mail_root=current_app.config.get("MAIL_ROOT"),
            newsletter_root=current_app.config.get("NEWSLETTER_ROOT"),
        )
    except Exception:
        return None
    problems = [c for c in report.checks if c.status in ("fail", "warn")]
    return {
        "ok": report.ok,
        "error_count": report.error_count,
        "warning_count": report.warning_count,
        "problems": problems[:5],
    }


@bp.get("/home")
def home_index():
    conn = g.db_ro
    return render_template(
        "home/index.html",
        page_title="Home",
        latest=_latest_run(conn),
        counts=_counts(conn),
        config=_config_summary(),
        doctor=_doctor_summary(conn),
        active=get_active_run(),
        busy=is_busy(),
    )
