"""Webpage article reading queue routes."""

from __future__ import annotations

import hmac
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.exceptions import HTTPException

from rollup.webpage.queue import (
    enqueue_url,
    get_by_url_hash,
    list_by_status,
    remove_item,
    retry_item,
)
from rollup.webpage.url import url_hash, validate_queue_url
from rollup.web.csrf import validate_csrf_token as csrf_ok
from rollup.web.db import mutation_connection, require_ro
from rollup.web.secrets import rotate_extension_token

bp = Blueprint("articles", __name__, url_prefix="/articles")

_TITLE_MAX = 280


def _json_error(error: str, status: int):
    return jsonify({"ok": False, "error": error}), status


def _bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _extension_token_ok(submitted: str | None) -> bool:
    expected = current_app.config.get("EXTENSION_TOKEN")
    if not expected or not submitted:
        return False
    return hmac.compare_digest(str(expected), str(submitted))


def _url_error_code(exc: ValueError) -> str:
    if "blocked" in str(exc).lower():
        return "url_ssrf"
    return "url_invalid"


@bp.get("")
def articles_index():
    conn = require_ro()
    pending = list_by_status(conn, "pending", limit=100)
    failed = list_by_status(conn, "failed", limit=50)
    ingested = list_by_status(conn, "ingested", limit=100)
    return render_template(
        "articles/index.html",
        pending=pending,
        failed=failed,
        ingested=ingested,
        extension_token=current_app.config.get("EXTENSION_TOKEN") or "",
    )


@bp.post("/add")
def articles_add():
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    raw_url = request.form.get("url", "").strip()
    display_title = request.form.get("display_title", "").strip() or None
    if not raw_url:
        flash("URL is required.")
        return redirect(url_for("articles.articles_index"))
    try:
        validate_queue_url(raw_url)
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("articles.articles_index"))
    try:
        with mutation_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            enqueue_url(conn, raw_url, display_title=display_title)
            conn.commit()
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("articles.articles_index"))
    except Exception:
        return (
            render_template(
                "errors/503.html",
                message="Database busy. Retry shortly.",
            ),
            503,
        )
    flash("Article saved. It will appear in digests whose lookback covers the save date.")
    return redirect(url_for("articles.articles_index"))


@bp.post("/capture")
def articles_capture():
    if not request.is_json:
        return _json_error("unsupported_media_type", 415)
    if not _extension_token_ok(_bearer_token(request.headers.get("Authorization"))):
        return _json_error("unauthorized", 401)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("invalid_json", 400)
    raw_url = payload.get("url")
    if not isinstance(raw_url, str) or not raw_url.strip():
        return _json_error("url_invalid", 400)
    raw_url = raw_url.strip()
    raw_title = payload.get("title")
    display_title = None
    if raw_title is not None:
        if not isinstance(raw_title, str):
            return _json_error("invalid_json", 400)
        trimmed = raw_title.strip()
        if trimmed:
            display_title = trimmed[:_TITLE_MAX]
    try:
        canonical = validate_queue_url(raw_url)
    except ValueError as exc:
        return _json_error(_url_error_code(exc), 400)
    digest = url_hash(canonical)
    try:
        with mutation_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = get_by_url_hash(conn, digest)
            if existing is None:
                outcome = "created"
            elif existing.status == "failed":
                outcome = "retried"
            else:
                outcome = "duplicate"
            item = enqueue_url(conn, raw_url, display_title=display_title)
            conn.commit()
    except ValueError as exc:
        return _json_error(_url_error_code(exc), 400)
    except HTTPException as exc:
        code = exc.code or 503
        return _json_error("web_unavailable", 503 if code not in (400, 503) else code)
    except Exception:
        return _json_error("web_unavailable", 503)
    return jsonify(
        {
            "ok": True,
            "outcome": outcome,
            "id": item.id,
            "url": item.url,
            "status": item.status,
        }
    )


@bp.post("/token/rotate")
def articles_token_rotate():
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    state_dir = Path(current_app.config["STATE_DIR"])
    token = rotate_extension_token(state_dir)
    current_app.config["EXTENSION_TOKEN"] = token
    flash("Extension token rotated. Update the Firefox add-on options.")
    return redirect(url_for("articles.articles_index"))


@bp.post("/<int:item_id>/remove")
def articles_remove(item_id: int):
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    try:
        with mutation_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            removed = remove_item(conn, item_id)
            conn.commit()
    except Exception:
        return (
            render_template(
                "errors/503.html",
                message="Database busy. Retry shortly.",
            ),
            503,
        )
    if not removed:
        flash("Queue item not found.")
    else:
        flash("Removed.")
    return redirect(url_for("articles.articles_index"))


@bp.post("/<int:item_id>/retry")
def articles_retry(item_id: int):
    if not csrf_ok(request.form.get("csrf_token")):
        return render_template("errors/400.html", message="CSRF validation failed"), 400
    try:
        with mutation_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            item = retry_item(conn, item_id)
            conn.commit()
    except Exception:
        return (
            render_template(
                "errors/503.html",
                message="Database busy. Retry shortly.",
            ),
            503,
        )
    if item is None:
        flash("Queue item not found.")
    elif item.status != "pending":
        flash("Only failed items can be retried.")
    else:
        flash("Queued for retry on the next digest.")
    return redirect(url_for("articles.articles_index"))
