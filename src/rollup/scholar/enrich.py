"""Replace Scholar alert emails with per-paper ParsedMessages when detailed."""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone

from rollup.config import Config, compute_date_window
from rollup.models import ParsedMessage
from rollup.scholar.cache import get_paper_body, store_paper_body
from rollup.scholar.config import SCHOLAR_FETCH_BACKOFF_SECONDS
from rollup.scholar.detect import is_scholar_alert
from rollup.scholar.parse import (
    ScholarPaper,
    assemble_paper_body,
    extract_papers_from_message,
    paper_message_key,
    paper_to_parsed_message,
)
from rollup.webpage.fetch import WebpageFetchError, fetch_webpage

logger = logging.getLogger(__name__)


def enrich_scholar_messages(
    messages: tuple[ParsedMessage, ...] | list[ParsedMessage],
    config: Config,
    *,
    allow_network: bool,
    conn: sqlite3.Connection | None,
    generated_at: datetime | None = None,
) -> tuple[list[ParsedMessage], list[tuple[str, str]]]:
    """Expand Scholar alerts in detailed mode; pass-through otherwise.

    Returns messages plus (code, message) warning pairs.
    """
    if not config.scholar.detailed:
        return list(messages), []

    when = generated_at or datetime.now(timezone.utc)
    window_start, window_end = compute_date_window(when, config.lookback_days)
    scholar_cfg = config.scholar
    out: list[ParsedMessage] = []
    warnings: list[tuple[str, str]] = []
    seen_paper_keys: set[str] = set()
    fetches_this_run = 0

    for msg in messages:
        if not is_scholar_alert(msg):
            out.append(msg)
            continue
        if msg.date_parsed is not None and not (
            window_start <= msg.date_parsed <= window_end
        ):
            out.append(msg)
            continue
        papers = extract_papers_from_message(msg)
        if not papers:
            warnings.append(
                (
                    "scholar_no_papers",
                    f"No paper links parsed from Scholar alert {msg.subject!r}",
                )
            )
            out.append(msg)
            continue

        for index, paper in enumerate(papers):
            key = paper_message_key(paper.url)
            if key in seen_paper_keys:
                continue
            seen_paper_keys.add(key)
            should_fetch = (
                index < scholar_cfg.max_papers_per_email and not paper.skip_fetch
            )
            page_title = ""
            page_text = ""
            extra: list[str] = []
            if not should_fetch and not paper.skip_fetch:
                extra.append("scholar_email_cap")
            if should_fetch:
                page_title, page_text, fetch_warnings, fetches_this_run = _resolve_body(
                    paper,
                    conn=conn,
                    allow_network=allow_network,
                    fetches_this_run=fetches_this_run,
                    max_fetches=scholar_cfg.max_fetches_per_run,
                    fetched_at=when,
                )
                extra.extend(fetch_warnings)
            body = assemble_paper_body(
                paper, page_title=page_title or None, page_text=page_text or None
            )
            out.append(
                paper_to_parsed_message(
                    paper,
                    msg,
                    body_text=body,
                    extra_warnings=tuple(extra),
                    max_body_chars=config.max_body_chars,
                )
            )
    return out, warnings


def _resolve_body(
    paper: ScholarPaper,
    *,
    conn: sqlite3.Connection | None,
    allow_network: bool,
    fetches_this_run: int,
    max_fetches: int,
    fetched_at: datetime,
) -> tuple[str, str, list[str], int]:
    extra: list[str] = []
    if conn is not None:
        cached = get_paper_body(conn, paper.url)
        if cached is not None:
            return cached[0], cached[1], extra, fetches_this_run
    if not allow_network:
        extra.append("scholar_fetch_skipped")
        return "", "", extra, fetches_this_run
    if fetches_this_run >= max_fetches:
        extra.append("scholar_fetch_cap")
        return "", "", extra, fetches_this_run
    if fetches_this_run > 0:
        time.sleep(SCHOLAR_FETCH_BACKOFF_SECONDS)
    fetches_this_run += 1
    try:
        result = fetch_webpage(paper.url)
    except WebpageFetchError as exc:
        logger.warning("Scholar paper fetch failed for %s: %s", paper.url[:80], exc)
        extra.append("scholar_fetch_failed")
        return "", "", extra, fetches_this_run
    title = result.title or ""
    body = result.body_text or ""
    if not body:
        extra.append("scholar_fetch_empty")
        return title, "", extra, fetches_this_run
    if conn is not None:
        store_paper_body(
            conn,
            paper.url,
            title=title,
            body_text=body,
            fetched_at=fetched_at,
        )
    extra.extend(result.warnings)
    return title, body, extra, fetches_this_run
