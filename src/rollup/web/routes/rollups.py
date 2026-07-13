"""Rollup archive and detail routes."""

from __future__ import annotations

from flask import Blueprint, g, render_template, request, url_for

from rollup.interaction import get_interaction
from rollup.links_sanitize import parse_links_json, parse_unsubscribe_link
from rollup.payload_limits import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from rollup.ratings import get_rating, list_reason_codes
from rollup.reader_body_store import reader_body_keys_present
from rollup.web_ids import IdError, encode_opaque, encode_run_opaque, validate_run_id

bp = Blueprint("rollups", __name__)


def _sum_nullable(*vals):
    nums = [v for v in vals if v is not None]
    if not nums:
        return None
    return sum(nums)


@bp.get("/rollups")
def list_rollups():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    try:
        page_size = min(
            MAX_PAGE_SIZE, max(1, int(request.args.get("page_size", DEFAULT_PAGE_SIZE)))
        )
    except ValueError:
        page_size = DEFAULT_PAGE_SIZE
    offset = (page - 1) * page_size
    total = g.db.execute("SELECT COUNT(*) FROM rollup_runs").fetchone()[0]
    rows = g.db.execute(
        """SELECT run_id, started_at, completed_at, status, window_start, window_end,
                  messages_included, sources_included, groups_created,
                  messages_skipped_outside_window, messages_skipped_seen_undated,
                  messages_deduped, summaries_ollama, summaries_cache,
                  warning_count, degraded, entry_index_version, stats_completeness,
                  markdown_relpath, html_relpath, manifest_relpath
           FROM rollup_runs
           ORDER BY started_at DESC, run_id DESC
           LIMIT ? OFFSET ?""",
        (page_size, offset),
    ).fetchall()
    runs = [
        {
            "run_id": r[0],
            "started_at": r[1],
            "completed_at": r[2],
            "status": r[3],
            "window_start": r[4],
            "window_end": r[5],
            "messages_included": r[6],
            "sources_included": r[7],
            "groups_created": r[8],
            "skipped": _sum_nullable(r[9], r[10], r[11]),
            "summaries_ollama": r[12],
            "summaries_cache": r[13],
            "warning_count": r[14],
            "degraded": bool(r[15]),
            "entry_index_version": r[16],
            "stats_completeness": r[17],
            "has_md": bool(r[18]),
            "has_html": bool(r[19]),
            "has_manifest": bool(r[20]),
        }
        for r in rows
    ]
    return render_template(
        "rollups/list.html",
        runs=runs,
        page=page,
        page_size=page_size,
        total=total,
        has_prev=page > 1,
        has_next=offset + page_size < total,
    )


@bp.get("/rollups/<run_id>")
def rollup_detail(run_id: str):
    try:
        run_id = validate_run_id(run_id)
    except IdError:
        return render_template("errors/404.html", message="Invalid run id"), 404
    show_dismissed = request.args.get("show_dismissed") == "1"
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    page_size = DEFAULT_PAGE_SIZE
    offset = (page - 1) * page_size

    cur = g.db.execute("SELECT * FROM rollup_runs WHERE run_id = ?", (run_id,))
    run = cur.fetchone()
    if run is None:
        return render_template("errors/404.html", message="Run not found"), 404
    cols = [c[0] for c in cur.description]
    run_dict = dict(zip(cols, run))

    cur = g.db.execute(
        """SELECT * FROM rollup_entries
           WHERE run_id = ?
           ORDER BY display_position
           LIMIT ? OFFSET ?""",
        (run_id, page_size, offset),
    )
    entry_cols = [c[0] for c in cur.description]
    entries_raw = cur.fetchall()
    reason_codes = list_reason_codes(g.db)
    message_keys = [row[entry_cols.index("message_key")] for row in entries_raw]
    bodies_present = reader_body_keys_present(g.db, message_keys)
    run_enc = encode_run_opaque(run_id)
    entries = []
    for row in entries_raw:
        e = dict(zip(entry_cols, row))
        inter = get_interaction(g.db, e["message_key"])
        if inter.is_dismissed and not show_dismissed:
            continue
        rating = get_rating(g.db, e["message_key"])
        e["interaction"] = inter
        e["rating"] = rating
        e["links"] = parse_links_json(e.get("links_json"))
        e["unsubscribe_link"] = parse_unsubscribe_link(e.get("links_json"))
        e["id_enc"] = encode_opaque(e["message_key"])
        if e.get("source_key_observed"):
            e["source_enc"] = encode_opaque(e["source_key_observed"])
        else:
            e["source_enc"] = None
        e["has_reader_body"] = e["message_key"] in bodies_present
        e["body_url"] = url_for(
            "messages.message_body",
            id_enc=e["id_enc"],
            run=run_enc,
        )
        entries.append(e)

    folder_sections: list[dict] = []
    folder_index: dict[str, int] = {}
    for e in entries:
        folder = e.get("folder_name") or "misc"
        if folder not in folder_index:
            folder_index[folder] = len(folder_sections)
            folder_sections.append({"folder_name": folder, "entries": []})
        folder_sections[folder_index[folder]]["entries"].append(e)

    total_entries = g.db.execute(
        "SELECT COUNT(*) FROM rollup_entries WHERE run_id = ?", (run_id,)
    ).fetchone()[0]

    return render_template(
        "rollups/detail.html",
        run=run_dict,
        entries=entries,
        folder_sections=folder_sections,
        reason_codes=reason_codes,
        show_dismissed=show_dismissed,
        page=page,
        page_size=page_size,
        total_entries=total_entries,
        has_prev=page > 1,
        has_next=offset + page_size < total_entries,
    )
