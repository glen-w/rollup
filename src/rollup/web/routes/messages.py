"""Message interaction and rating POST routes."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, Response, g, redirect, render_template, request, url_for
from markupsafe import Markup

from rollup.interaction import dismiss, mark_read, mark_unread, save, undismiss, unsave
from rollup.ratings import RatingError, set_rating_with_reasons
from rollup.reader_bodies import READER_TEXT_VERSION, prepare_reader_text
from rollup.reader_body_store import get_reader_body
from rollup.web.csrf import validate_csrf_token as csrf_ok
from rollup.web.format import reader_body_fragment_html
from rollup.web.navigation import build_reader_nav, parse_run_query
from rollup.web_ids import IdError, decode_opaque, encode_opaque

bp = Blueprint("messages", __name__)


def _safe_next(nxt: str | None) -> str:
    """Allow only same-origin relative paths (reject protocol-relative //…)."""
    if (
        not nxt
        or not nxt.startswith("/")
        or nxt.startswith("//")
        or "\\" in nxt
        or "://" in nxt
    ):
        return url_for("rollups.list_rollups")
    return nxt


def _redirect_back():
    nxt = request.form.get("next") or None
    # Prefer explicit form next; ignore absolute referrers (open-redirect surface).
    return redirect(_safe_next(nxt))


def _csrf_or_400():
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    return None


def _canonical_opaque(id_enc: str, *, kind: str) -> str | None:
    try:
        key = decode_opaque(id_enc, kind=kind)
    except IdError:
        return None
    canonical = encode_opaque(key)
    if canonical != id_enc:
        return None
    return key


def _body_cache_headers(resp: Response) -> Response:
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


def _entry_metadata(message_key: str) -> dict | None:
    row = g.db.execute(
        """SELECT e.subject, e.sender, e.date_parsed, e.date_raw
           FROM rollup_entries e
           JOIN rollup_runs r ON r.run_id = e.run_id
           WHERE e.message_key = ?
           ORDER BY r.started_at DESC, r.run_id DESC
           LIMIT 1""",
        (message_key,),
    ).fetchone()
    if row is None:
        return None
    return {
        "subject": row[0],
        "sender": row[1],
        "date_parsed": row[2],
        "date_raw": row[3],
    }


@bp.route("/messages/<id_enc>/body", methods=["GET", "HEAD"])
def message_body(id_enc: str):
    key = _canonical_opaque(id_enc, kind="message")
    if key is None:
        return render_template("errors/404.html", message="Not found"), 404
    record = get_reader_body(g.db, key)
    if record is None:
        return render_template("errors/404.html", message="Not found"), 404
    partial = request.args.get("partial") == "1"
    run_id = parse_run_query(request.args.get("run"))
    show_dismissed = request.args.get("show_dismissed") == "1"
    if request.method == "HEAD":
        return _body_cache_headers(Response(status=200))
    meta = _entry_metadata(key)
    nav = None
    if run_id:
        nav = build_reader_nav(
            g.db,
            run_id=run_id,
            message_key=key,
            show_dismissed=show_dismissed,
        )
    body_text = record.body_text
    truncated = record.truncated
    if record.reader_text_version < READER_TEXT_VERSION:
        prepared = prepare_reader_text(body_text)
        body_text = prepared.text
        truncated = truncated or prepared.truncated
    fragment = reader_body_fragment_html(body_text, truncated=truncated)
    if partial:
        return _body_cache_headers(Response(Markup(fragment), status=200))
    return _body_cache_headers(
        Response(
            render_template(
                "messages/body.html",
                meta=meta,
                body_html=Markup(fragment),
                id_enc=id_enc,
                nav=nav,
            ),
            status=200,
        )
    )


@bp.post("/messages/<id_enc>/read")
def message_read(id_enc: str):
    err = _csrf_or_400()
    if err:
        return err
    try:
        key = decode_opaque(id_enc, kind="message")
    except IdError:
        return render_template("errors/404.html", message="Invalid message id"), 404
    action = request.form.get("action", "read")
    now = datetime.now(timezone.utc)
    try:
        if action == "unread":
            mark_unread(g.db, key, now=now)
        else:
            mark_read(g.db, key, now=now)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return render_template("errors/503.html", message="Database busy; retry."), 503
        raise
    return _redirect_back()


@bp.post("/messages/<id_enc>/save")
def message_save(id_enc: str):
    err = _csrf_or_400()
    if err:
        return err
    try:
        key = decode_opaque(id_enc, kind="message")
    except IdError:
        return render_template("errors/404.html", message="Invalid message id"), 404
    action = request.form.get("action", "save")
    now = datetime.now(timezone.utc)
    try:
        if action == "unsave":
            unsave(g.db, key, now=now)
        else:
            save(g.db, key, now=now)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return render_template("errors/503.html", message="Database busy; retry."), 503
        raise
    return _redirect_back()


@bp.post("/messages/<id_enc>/dismiss")
def message_dismiss(id_enc: str):
    err = _csrf_or_400()
    if err:
        return err
    try:
        key = decode_opaque(id_enc, kind="message")
    except IdError:
        return render_template("errors/404.html", message="Invalid message id"), 404
    action = request.form.get("action", "dismiss")
    now = datetime.now(timezone.utc)
    try:
        if action == "undismiss":
            undismiss(g.db, key, now=now)
        else:
            dismiss(g.db, key, now=now)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return render_template("errors/503.html", message="Database busy; retry."), 503
        raise
    return _redirect_back()


@bp.post("/messages/<id_enc>/rating")
def message_rating(id_enc: str):
    err = _csrf_or_400()
    if err:
        return err
    try:
        key = decode_opaque(id_enc, kind="message")
    except IdError:
        return render_template("errors/404.html", message="Invalid message id"), 404
    try:
        stars = int(request.form.get("stars", ""))
    except ValueError:
        return render_template("errors/400.html", message="Invalid stars"), 400
    reasons = request.form.getlist("reasons")
    now = datetime.now(timezone.utc)
    try:
        set_rating_with_reasons(g.db, key, stars, reasons, now=now)
    except RatingError as exc:
        return render_template("errors/400.html", message=str(exc)), 400
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return render_template("errors/503.html", message="Database busy; retry."), 503
        raise
    return _redirect_back()
