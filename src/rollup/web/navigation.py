"""Rollup-aware reader navigation helpers."""

from __future__ import annotations

import sqlite3

from flask import url_for

from rollup.interaction import get_interaction
from rollup.web_ids import IdError, decode_run_opaque, encode_run_opaque, validate_run_id


def _entry_visible(message_key: str, conn: sqlite3.Connection, *, show_dismissed: bool) -> bool:
    if show_dismissed:
        return True
    return not get_interaction(conn, message_key).is_dismissed


def build_reader_nav(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    message_key: str,
    show_dismissed: bool = False,
    page: int = 1,
) -> dict | None:
    """Return nav links if message belongs to run; else None."""
    try:
        validate_run_id(run_id)
    except IdError:
        return None
    member = conn.execute(
        "SELECT 1 FROM rollup_entries WHERE run_id = ? AND message_key = ?",
        (run_id, message_key),
    ).fetchone()
    if member is None:
        return None
    rows = conn.execute(
        """SELECT message_key, display_position, section_key, group_id
           FROM rollup_entries WHERE run_id = ?
           ORDER BY display_position ASC, message_key ASC""",
        (run_id,),
    ).fetchall()
    visible = [
        r for r in rows if _entry_visible(r[0], conn, show_dismissed=show_dismissed)
    ]
    keys = [r[0] for r in visible]
    if message_key not in keys:
        return None
    idx = keys.index(message_key)
    from rollup.web_ids import encode_opaque

    def _body_url(mk: str) -> str:
        q = f"run={encode_run_opaque(run_id)}"
        if show_dismissed:
            q += "&show_dismissed=1"
        return url_for("messages.message_body", id_enc=encode_opaque(mk)) + "?" + q

    cur = visible[idx]
    back = url_for(
        "rollups.rollup_detail",
        run_id=run_id,
        page=page,
        show_dismissed=1 if show_dismissed else None,
    )
    nav = {
        "back": back,
        "section_key": cur[2],
        "prev": _body_url(keys[idx - 1]) if idx > 0 else None,
        "next": _body_url(keys[idx + 1]) if idx + 1 < len(keys) else None,
    }
    return nav


def parse_run_query(token: str | None) -> str | None:
    if not token:
        return None
    try:
        canonical = encode_run_opaque(decode_run_opaque(token))
    except IdError:
        return None
    if canonical != token:
        return None
    return decode_run_opaque(token)
