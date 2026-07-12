"""Message interaction and rating POST routes."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, g, redirect, render_template, request, url_for

from rollup.interaction import dismiss, mark_read, mark_unread, save, undismiss, unsave
from rollup.ratings import RatingError, set_rating_with_reasons
from rollup.web.csrf import validate_csrf_token as csrf_ok
from rollup.web_ids import IdError, decode_opaque

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
