"""Webpage article reading queue routes."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for

from rollup.webpage.queue import (
    enqueue_url,
    list_by_status,
    remove_item,
    retry_item,
)
from rollup.webpage.url import validate_queue_url
from rollup.web.csrf import validate_csrf_token as csrf_ok
from rollup.web.db import mutation_connection, require_ro

bp = Blueprint("articles", __name__, url_prefix="/articles")


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
        flash("Removed from queue.")
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
