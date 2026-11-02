"""Ingest transports: mbox discovery/parse plus LinkedIn, Reddit, webpage, Scholar."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from rollup.config import Config, compute_date_window
from rollup.discovery import filter_folders, iter_mbox_files
from rollup.models import MboxFolder, ParsedMessage
from rollup.pipeline.types import (
    DiscoveryResult,
    ParseCounts,
    ParseResult,
    StageError,
    StageWarning,
)

if TYPE_CHECKING:
    from rollup.gmail.config import GmailTarget
    from rollup.gmail.session import GmailClient
    from rollup.linkedin.config import LinkedInSearch
    from rollup.linkedin.fetch import LinkedInClient
    from rollup.webpage.models import WebpageQueueItem

logger = logging.getLogger(__name__)


def stage_discover(
    config: Config, *, generated_at: datetime | None = None
) -> DiscoveryResult:
    from rollup.linkedin.config import filter_linkedin_searches
    from rollup.reddit.config import filter_reddit_subs
    from rollup.webpage.config import MAX_WEBPAGE_FETCHES, WEBPAGE_FOLDER_NAME
    from rollup.webpage.queue import load_for_digest

    # A Gmail-only setup may have no local mbox root at all.
    folders = list(iter_mbox_files(config.root)) if config.root.is_dir() else []
    folders = filter_folders(folders, config.folders_include, config.folders_exclude)
    linkedin_searches: tuple[LinkedInSearch, ...] = ()
    if config.linkedin_enabled and config.linkedin.searches:
        linkedin_searches = filter_linkedin_searches(
            config.linkedin.searches,
            folders_include=config.folders_include,
            folders_exclude=config.folders_exclude,
            layout=config.linkedin.layout,
        )
    reddit_subs: tuple = ()
    if config.reddit_enabled and config.reddit.subs:
        reddit_subs = filter_reddit_subs(
            config.reddit,
            folders_include=config.folders_include,
            folders_exclude=config.folders_exclude,
        )
    webpage_items: tuple[WebpageQueueItem, ...] = ()
    if config.webpage_enabled:
        exclude = set(config.folders_exclude)
        include = set(config.folders_include)
        webpage_allowed = WEBPAGE_FOLDER_NAME not in exclude and (
            not include or WEBPAGE_FOLDER_NAME in include
        )
        if webpage_allowed:
            db_path = config.db_path
            if db_path.is_file():
                from datetime import timezone

                from rollup.state import init_db

                when = generated_at or datetime.now(timezone.utc)
                window_start, window_end = compute_date_window(
                    when, config.lookback_days
                )
                conn = init_db(db_path)
                try:
                    webpage_items = load_for_digest(
                        conn,
                        window_start=window_start,
                        window_end=window_end,
                        fetch_limit=MAX_WEBPAGE_FETCHES,
                    )
                finally:
                    conn.close()
    rss_feeds: tuple = ()
    if config.rss_enabled and config.rss.feeds:
        from rollup.rss.config import filter_rss_feeds

        rss_feeds = filter_rss_feeds(
            config.rss,
            folders_include=config.folders_include,
            folders_exclude=config.folders_exclude,
        )
    gmail_targets: tuple[GmailTarget, ...] = ()
    if config.gmail_enabled and config.gmail.configured:
        from rollup.gmail.config import targets_from_config

        known: dict[str, str] = {}
        if config.db_path.is_file():
            from rollup.gmail.cache import known_labels
            from rollup.state import init_db

            conn = init_db(config.db_path)
            try:
                known = known_labels(conn)
            finally:
                conn.close()
        gmail_targets = targets_from_config(
            config.gmail,
            folders_include=config.folders_include,
            folders_exclude=config.folders_exclude,
            known_labels=known,
        )
    return DiscoveryResult(
        folders=tuple(folders),
        linkedin_searches=linkedin_searches,
        reddit_subs=reddit_subs,
        webpage_items=webpage_items,
        gmail_targets=gmail_targets,
        rss_feeds=rss_feeds,
    )


def stage_parse_mbox(config: Config, folders: tuple[MboxFolder, ...]) -> ParseResult:
    from rollup.mbox_identity import classify_mbox_mutation, snapshot_mbox
    from rollup.parse import parse_mbox_folder

    messages: list[ParsedMessage] = []
    warnings: list[StageWarning] = []
    errors: list[StageError] = []
    seen = 0
    fatal = 0
    anomalies = 0
    folders_failed = 0
    mutated_folders: list[str] = []
    mutation_codes: list[str] = []

    for folder in folders:
        logger.info("Parsing %s (%s)", folder.folder_name, folder.mbox_path)
        before = snapshot_mbox(folder.mbox_path)
        msgs, err_count, folder_errors = parse_mbox_folder(
            folder, config.max_body_chars, config.max_display_links
        )
        after = snapshot_mbox(folder.mbox_path)
        mutation = classify_mbox_mutation(before, after)
        if mutation:
            mutated_folders.append(folder.folder_name)
            mutation_codes.append(mutation)
            warnings.append(
                StageWarning(
                    code=mutation,
                    message=f"mbox changed during parse ({mutation})",
                    folder=folder.folder_name,
                )
            )
            logger.warning(
                "Mbox mutation %s on %s; excluding folder results from publication",
                mutation,
                folder.folder_name,
            )
            # Exclude changed-folder results from the published digest.
            continue
        # Approximate seen: parsed + errors for this folder.
        folder_seen = len(msgs) + err_count
        if folder_errors:
            folders_failed += 1
            fatal += 1
            errors.append(
                StageError(
                    code="mbox_open",
                    message=folder_errors[0],
                    folder=folder.folder_name,
                )
            )
            logger.error("Folder %s: %s", folder.folder_name, folder_errors[0])
            continue
        seen += folder_seen
        fatal += err_count
        for msg in msgs:
            anomalies += len(msg.parse_warnings)
            messages.append(msg)

    counts = ParseCounts(
        messages_seen=seen,
        messages_parsed=len(messages),
        parse_fatal_errors=fatal,
        parse_anomalies=anomalies,
        folders_failed=folders_failed,
    )
    return ParseResult(
        messages=tuple(messages),
        counts=counts,
        warnings=tuple(warnings),
        errors=tuple(errors),
        mutated_folders=tuple(mutated_folders),
        mutation_codes=tuple(mutation_codes),
    )


def stage_parse_linkedin(
    config: Config,
    searches: tuple[LinkedInSearch, ...],
    *,
    dry_run: bool,
    client: LinkedInClient | None = None,
    generated_at: datetime | None = None,
) -> tuple[list[ParsedMessage], list[StageWarning], bool]:
    """Fetch LinkedIn searches and map to ParsedMessage. Returns (msgs, warnings, degraded)."""
    if not searches:
        return [], [], False

    from rollup.error_sanitize import sanitize_provider_message
    from rollup.linkedin.article import enrich_posts_with_articles
    from rollup.linkedin.cache import (
        partition_linkedin_searches,
        save_listing_snapshot,
        should_fetch_search,
    )
    from rollup.linkedin.fetch import LinkedInFetchError, fetch_search_posts
    from rollup.linkedin.parse import linkedin_post_to_parsed_message
    from rollup.linkedin.session import (
        linkedin_cookie_configured,
    )
    from rollup.state import init_db

    warnings: list[StageWarning] = []
    messages: list[ParsedMessage] = []
    degraded = False
    when = generated_at or datetime.now().astimezone()
    ttl_hours = config.linkedin.fetch_ttl_hours
    refresh = config.linkedin_refresh

    if dry_run:
        from rollup.linkedin.session import jsession_id_configured
        from rollup.linkedin.url import from_member_ids

        if not linkedin_cookie_configured():
            warnings.append(
                StageWarning(
                    code="linkedin_no_cookie",
                    message=(
                        "LinkedIn enabled but ROLLUP_LINKEDIN_LI_AT is not set "
                        "(dry-run; no fetch)"
                    ),
                )
            )
        needs_voyager = any(from_member_ids(s.url) for s in searches)
        if needs_voyager and not jsession_id_configured():
            warnings.append(
                StageWarning(
                    code="linkedin_no_jsession",
                    message=(
                        "fromMember search requires ROLLUP_LINKEDIN_JSESSIONID "
                        "(dry-run; no fetch)"
                    ),
                )
            )
        conn = init_db(config.db_path)
        try:
            cached, to_fetch, cache_logs = partition_linkedin_searches(
                conn,
                searches,
                ttl_hours=ttl_hours,
                refresh=refresh,
                now=when,
            )
            for line in cache_logs:
                logger.info(line)
            for search in to_fetch:
                warnings.append(
                    StageWarning(
                        code="linkedin_dry_run",
                        message=f"Would fetch LinkedIn search {search.slug}",
                        folder=search.folder_name,
                    )
                )
            for slug in cached:
                warnings.append(
                    StageWarning(
                        code="linkedin_dry_run",
                        message=f"Would reuse cached LinkedIn search {slug}",
                        folder=f"linkedin:{slug}",
                    )
                )
        finally:
            conn.close()
        degraded = (not linkedin_cookie_configured()) or (
            needs_voyager and not jsession_id_configured()
        )
        return messages, warnings, degraded

    conn = init_db(config.db_path)
    try:
        cached_posts, searches_to_fetch, cache_logs = partition_linkedin_searches(
            conn,
            searches,
            ttl_hours=ttl_hours,
            refresh=refresh,
            now=when,
        )
        for line in cache_logs:
            logger.info(line)

        posts_by_search: dict[str, list] = dict(cached_posts)
        for search in searches_to_fetch:
            try:
                posts = fetch_search_posts(
                    search,
                    lookback_days=config.lookback_days,
                    client=client,
                )
                if config.linkedin.article_fetch:
                    posts, article_warnings = enrich_posts_with_articles(
                        posts,
                        enabled=True,
                        conn=conn,
                        fetched_at=when,
                    )
                else:
                    article_warnings = [() for _ in posts]
                save_listing_snapshot(
                    conn,
                    search=search,
                    posts=posts,
                    fetched_at=when,
                )
                posts_by_search[search.slug] = posts
                for post, post_warnings in zip(posts, article_warnings, strict=True):
                    messages.append(
                        linkedin_post_to_parsed_message(
                            post,
                            search_slug=search.slug,
                            max_body_chars=config.max_body_chars,
                            layout=config.linkedin.layout,
                            extra_warnings=post_warnings,
                        )
                    )
            except LinkedInFetchError as exc:
                degraded = True
                _, snapshot = should_fetch_search(
                    conn,
                    search=search,
                    ttl_hours=ttl_hours,
                    refresh=True,
                    now=when,
                )
                if snapshot is not None and snapshot.url == search.url:
                    from rollup.linkedin.cache import load_posts_by_keys

                    stale_posts = load_posts_by_keys(conn, snapshot.post_keys)
                    posts_by_search[search.slug] = stale_posts
                    warnings.append(
                        StageWarning(
                            code="linkedin_cache_stale",
                            message=(
                                f"Using stale LinkedIn cache for {search.slug}: "
                                f"{sanitize_provider_message(str(exc))}"
                            ),
                            folder=search.folder_name,
                        )
                    )
                    for post in stale_posts:
                        messages.append(
                            linkedin_post_to_parsed_message(
                                post,
                                search_slug=search.slug,
                                max_body_chars=config.max_body_chars,
                                layout=config.linkedin.layout,
                            )
                        )
                else:
                    warnings.append(
                        StageWarning(
                            code="linkedin_fetch_failed",
                            message=sanitize_provider_message(str(exc)),
                            folder=search.folder_name,
                        )
                    )
                    logger.warning(
                        "LinkedIn fetch failed for %s: %s",
                        search.slug,
                        sanitize_provider_message(str(exc)),
                    )

        for slug, posts in cached_posts.items():
            if slug in {s.slug for s in searches_to_fetch}:
                continue
            for post in posts:
                messages.append(
                    linkedin_post_to_parsed_message(
                        post,
                        search_slug=slug,
                        max_body_chars=config.max_body_chars,
                        layout=config.linkedin.layout,
                    )
                )
    finally:
        conn.close()

    return messages, warnings, degraded


def stage_parse_reddit(
    config: Config,
    subs: tuple,
    *,
    dry_run: bool,
    generated_at: datetime | None = None,
    client=None,
) -> tuple[list[ParsedMessage], list[StageWarning], bool]:
    """Fetch Reddit subs and map to ParsedMessage. Returns (msgs, warnings, degraded)."""
    if not subs:
        return [], [], False

    from rollup.error_sanitize import sanitize_provider_message
    from rollup.reddit.cache import (
        fetch_and_cache_reddit_subs,
        partition_reddit_subs,
        resolve_stale_sub_posts,
    )
    from rollup.reddit.fetch import SUB_FETCH_BACKOFF_SECONDS, reddit_fetch_eta_phrase
    from rollup.reddit.parse import reddit_post_to_parsed_message
    from rollup.state import init_db

    warnings: list[StageWarning] = []
    messages: list[ParsedMessage] = []
    degraded = False
    when = generated_at or datetime.now().astimezone()
    window_start, window_end = compute_date_window(when, config.lookback_days)
    ttl_hours = config.reddit.fetch_ttl_hours
    refresh = config.reddit_refresh

    conn = init_db(config.db_path)
    try:
        cached_posts, subs_to_fetch, cache_logs = partition_reddit_subs(
            conn,
            subs,
            config=config.reddit,
            lookback_days=config.lookback_days,
            ttl_hours=ttl_hours,
            refresh=refresh,
            now=when,
            window_start=window_start,
            window_end=window_end,
        )
        for line in cache_logs:
            logger.info(line)

        if dry_run:
            fetch_count = len(subs_to_fetch)
            phrase = reddit_fetch_eta_phrase(fetch_count)
            if phrase:
                warnings.append(
                    StageWarning(
                        code="reddit_dry_run",
                        message=(
                            f"Would fetch {fetch_count} Reddit subs, {phrase} "
                            f"({int(SUB_FETCH_BACKOFF_SECONDS)}s between subs)"
                        ),
                        folder=config.reddit.layout,
                    )
                )
            elif cached_posts:
                warnings.append(
                    StageWarning(
                        code="reddit_dry_run",
                        message=(
                            f"Would reuse cached Reddit listings for "
                            f"{len(cached_posts)} sub(s)"
                        ),
                        folder=config.reddit.layout,
                    )
                )
            for sub in subs_to_fetch:
                warnings.append(
                    StageWarning(
                        code="reddit_dry_run",
                        message=f"Would fetch r/{sub.name} (JSON/RSS listing)",
                        folder=config.reddit.layout,
                    )
                )
            for sub_name in cached_posts:
                if sub_name not in {s.name for s in subs_to_fetch}:
                    warnings.append(
                        StageWarning(
                            code="reddit_dry_run",
                            message=f"Would reuse cached listing for r/{sub_name}",
                            folder=config.reddit.layout,
                        )
                    )
            return messages, warnings, degraded

        posts_by_sub: dict[str, list] = dict(cached_posts)
        if subs_to_fetch:
            fetched, fetch_failures, stale_candidates = fetch_and_cache_reddit_subs(
                conn,
                tuple(subs_to_fetch),
                config=config.reddit,
                lookback_days=config.lookback_days,
                window_start=window_start,
                window_end=window_end,
                fetched_at=when,
                client=client,
            )
            posts_by_sub.update(fetched)
            stale_by_name = {name: snap for name, snap in stale_candidates}
            if fetch_failures:
                degraded = True
                for failure in fetch_failures:
                    sub_name = failure.split(":", 1)[0][2:] if failure.startswith("r/") else ""
                    snapshot = stale_by_name.get(sub_name)
                    if snapshot is not None:
                        stale_sub = next((s for s in subs_to_fetch if s.name == sub_name), None)
                        if stale_sub is not None:
                            posts_by_sub[sub_name] = resolve_stale_sub_posts(
                                conn,
                                sub,
                                config.reddit,
                                config.lookback_days,
                                snapshot,
                                window_start=window_start,
                                window_end=window_end,
                            )
                            warnings.append(
                                StageWarning(
                                    code="reddit_cache_stale",
                                    message=(
                                        f"Using stale Reddit cache for r/{sub_name}: "
                                        f"{sanitize_provider_message(failure)}"
                                    ),
                                )
                            )
                            continue
                    warnings.append(
                        StageWarning(
                            code="reddit_fetch_failed",
                            message=sanitize_provider_message(failure),
                        )
                    )
                if not posts_by_sub:
                    return messages, warnings, degraded

        for posts in posts_by_sub.values():
            for post in posts:
                messages.append(
                    reddit_post_to_parsed_message(
                        post,
                        layout=config.reddit.layout,
                        max_body_chars=config.max_body_chars,
                    )
                )
    finally:
        conn.close()

    return messages, warnings, degraded


def merge_parse(
    prior: ParseResult,
    messages: list[ParsedMessage],
    warnings: list[StageWarning],
) -> ParseResult:
    """Append transport messages and warnings onto a prior ParseResult."""
    if not messages and not warnings:
        return prior
    combined = list(prior.messages) + messages
    return ParseResult(
        messages=tuple(combined),
        counts=ParseCounts(
            messages_seen=prior.counts.messages_seen + len(messages),
            messages_parsed=len(combined),
            parse_fatal_errors=prior.counts.parse_fatal_errors,
            parse_anomalies=prior.counts.parse_anomalies,
            folders_failed=prior.counts.folders_failed,
        ),
        warnings=prior.warnings + tuple(warnings),
        errors=prior.errors,
        mutated_folders=prior.mutated_folders,
        mutation_codes=prior.mutation_codes,
    )


def merge_reddit_parse(
    prior: ParseResult,
    reddit_messages: list[ParsedMessage],
    reddit_warnings: list[StageWarning],
) -> ParseResult:
    """Combine prior parse results with Reddit messages."""
    return merge_parse(prior, reddit_messages, reddit_warnings)


def merge_linkedin_parse(
    mbox_parse: ParseResult,
    linkedin_messages: list[ParsedMessage],
    linkedin_warnings: list[StageWarning],
) -> ParseResult:
    """Combine mbox and LinkedIn parse results."""
    return merge_parse(mbox_parse, linkedin_messages, linkedin_warnings)


def stage_parse_webpage(
    config: Config,
    items: tuple[WebpageQueueItem, ...],
    *,
    dry_run: bool,
    generated_at: datetime,
) -> tuple[list[ParsedMessage], list[StageWarning], bool, list[tuple[int, str]]]:
    """Map webpage queue items to ParsedMessage, fetching only on cache miss."""
    if not items:
        return [], [], False, []

    from rollup.error_sanitize import sanitize_provider_message
    from rollup.state import init_db
    from rollup.webpage.config import WEBPAGE_FOLDER_NAME
    from rollup.webpage.fetch import WebpageFetchError, fetch_webpage
    from rollup.webpage.parse import webpage_to_parsed_message
    from rollup.webpage.queue import mark_failed, store_fetched

    warnings: list[StageWarning] = []
    messages: list[ParsedMessage] = []
    ingest_map: list[tuple[int, str]] = []
    degraded = False

    conn = init_db(config.db_path)
    try:
        for item in items:
            if item.has_cached_body:
                msg = webpage_to_parsed_message(
                    url=item.url,
                    title=item.fetched_title or None,
                    body_text=item.body_text or "",
                    saved_at=item.created_at,
                    display_title=item.display_title,
                    max_body_chars=config.max_body_chars,
                )
                messages.append(msg)
                ingest_map.append((item.id, msg.message_key))
                continue

            if dry_run:
                warnings.append(
                    StageWarning(
                        code="webpage_dry_run",
                        message=f"Would fetch webpage queue item {item.url}",
                        folder=WEBPAGE_FOLDER_NAME,
                    )
                )
                continue

            try:
                result = fetch_webpage(item.url)
            except WebpageFetchError as exc:
                degraded = True
                mark_failed(
                    conn,
                    item.id,
                    error_code=exc.code,
                    error_message=exc.message,
                )
                warnings.append(
                    StageWarning(
                        code=exc.code,
                        message=sanitize_provider_message(exc.message),
                        folder=WEBPAGE_FOLDER_NAME,
                    )
                )
                logger.warning(
                    "Webpage fetch failed for %s: %s",
                    item.url[:80],
                    sanitize_provider_message(exc.message),
                )
                continue

            msg = webpage_to_parsed_message(
                url=result.url,
                title=result.title or None,
                body_text=result.body_text,
                saved_at=item.created_at,
                display_title=item.display_title,
                max_body_chars=config.max_body_chars,
                extra_warnings=result.warnings,
            )
            store_fetched(
                conn,
                item.id,
                title=result.title or None,
                body_text=result.body_text,
                content_hash=msg.content_hash,
                message_key=msg.message_key,
                fetched_at=generated_at,
            )
            messages.append(msg)
            ingest_map.append((item.id, msg.message_key))
            for code in result.warnings:
                warnings.append(
                    StageWarning(
                        code=code,
                        message=code,
                        folder=WEBPAGE_FOLDER_NAME,
                    )
                )
    finally:
        conn.close()

    return messages, warnings, degraded, ingest_map


def stage_enrich_scholar(
    config: Config,
    parse_result: ParseResult,
    *,
    allow_network: bool,
    dry_run: bool,
    generated_at: datetime,
) -> ParseResult:
    """Expand Scholar alerts into per-paper messages when mode is detailed."""
    if not config.scholar.detailed:
        return parse_result
    from rollup.scholar.detect import PAPER_MESSAGE_KEY_PREFIX
    from rollup.scholar.enrich import enrich_scholar_messages

    conn = None
    if not dry_run:
        from rollup.state import init_db

        conn = init_db(config.db_path)
    try:
        messages, raw_warnings = enrich_scholar_messages(
            parse_result.messages,
            config,
            allow_network=allow_network,
            conn=conn,
            generated_at=generated_at,
        )
    finally:
        if conn is not None:
            conn.close()

    extra: list[StageWarning] = [
        StageWarning(code=code, message=message) for code, message in raw_warnings
    ]
    paper_count = sum(
        1 for m in messages if m.message_key.startswith(PAPER_MESSAGE_KEY_PREFIX)
    )
    if paper_count:
        extra.append(
            StageWarning(
                code="scholar_papers",
                message=f"Scholar detailed mode: {paper_count} paper(s)",
                count=paper_count,
            )
        )
    return ParseResult(
        messages=tuple(messages),
        counts=ParseCounts(
            messages_seen=parse_result.counts.messages_seen,
            messages_parsed=len(messages),
            parse_fatal_errors=parse_result.counts.parse_fatal_errors,
            parse_anomalies=parse_result.counts.parse_anomalies,
            folders_failed=parse_result.counts.folders_failed,
        ),
        warnings=parse_result.warnings + tuple(extra),
        errors=parse_result.errors,
        mutated_folders=parse_result.mutated_folders,
        mutation_codes=parse_result.mutation_codes,
    )


def merge_webpage_parse(
    parse_result: ParseResult,
    webpage_messages: list[ParsedMessage],
    webpage_warnings: list[StageWarning],
) -> ParseResult:
    """Combine prior parse results with webpage messages."""
    return merge_parse(parse_result, webpage_messages, webpage_warnings)


def stage_parse_gmail(
    config: Config,
    targets: tuple[GmailTarget, ...],
    *,
    dry_run: bool,
    generated_at: datetime | None = None,
    client: GmailClient | None = None,
) -> tuple[list[ParsedMessage], list[StageWarning], bool]:
    """Sync configured Gmail labels and map raw mail to ParsedMessage.

    Returns ``(msgs, warnings, degraded)``. Labels served from a fresh cache
    make no network calls; failures fall back to the stale cache when one
    exists. Gmail is never written to (readonly scope, GET-only client).
    """
    if not targets:
        return [], [], False

    from rollup.error_sanitize import sanitize_provider_message
    from rollup.gmail.cache import (
        fetch_and_cache_gmail_targets,
        get_label_snapshot,
        load_raw_messages,
        partition_gmail_targets,
        sweep_old_messages,
    )
    from rollup.gmail.parse import raw_to_parsed_message
    from rollup.gmail.session import (
        CLIENT_ID_ENV,
        CLIENT_SECRET_ENV,
        build_gmail_client,
        credentials_configured,
        token_configured,
    )
    from rollup.state import init_db

    warnings: list[StageWarning] = []
    messages: list[ParsedMessage] = []
    degraded = False
    when = generated_at or datetime.now().astimezone()
    window_start, _window_end = compute_date_window(when, config.lookback_days)
    ttl_hours = config.gmail.fetch_ttl_hours
    refresh = config.gmail_refresh
    strip = config.gmail.effective_strip_prefix

    def _emit(label_name: str, folder_name: str, raws: list) -> None:
        for raw in raws:
            try:
                messages.append(
                    raw_to_parsed_message(
                        raw,
                        folder_name=folder_name,
                        label_name=label_name,
                        max_body_chars=config.max_body_chars,
                        max_display_links=config.max_display_links,
                    )
                )
            except Exception as exc:  # one bad message must not sink the label
                warnings.append(
                    StageWarning(
                        code="gmail_parse_error",
                        message=f"{raw.gmail_id}: {exc}",
                        folder=folder_name,
                    )
                )

    conn = init_db(config.db_path)
    try:
        cached, to_fetch, cache_logs = partition_gmail_targets(
            conn, targets, ttl_hours=ttl_hours, refresh=refresh, now=when
        )
        for line in cache_logs:
            logger.info(line)
        folder_for = {t.label_name: t.folder_name for t in targets}

        if dry_run:
            if client is None:
                if not credentials_configured():
                    warnings.append(
                        StageWarning(
                            code="gmail_no_credentials",
                            message=(
                                f"Gmail enabled but {CLIENT_ID_ENV}/{CLIENT_SECRET_ENV} "
                                "are not set (dry-run; no fetch)"
                            ),
                        )
                    )
                    degraded = True
                if not token_configured(config.state_dir):
                    warnings.append(
                        StageWarning(
                            code="gmail_no_token",
                            message="Gmail token missing; run `rollup gmail auth` (dry-run; no fetch)",
                        )
                    )
                    degraded = True
            for target in to_fetch:
                what = f"labels under {target.label_name}/" if target.is_prefix else f"label {target.label_name}"
                warnings.append(
                    StageWarning(
                        code="gmail_dry_run",
                        message=f"Would sync Gmail {what}",
                        folder=target.folder_name,
                    )
                )
            for label_name, raws in cached.items():
                warnings.append(
                    StageWarning(
                        code="gmail_dry_run",
                        message=f"Would reuse cached Gmail label {label_name} ({len(raws)} msgs)",
                        folder=folder_for.get(label_name),
                    )
                )
            return messages, warnings, degraded

        for label_name, raws in cached.items():
            _emit(label_name, folder_for[label_name], raws)

        if to_fetch:
            gmail_client = client or build_gmail_client(config.state_dir)
            notices: list[tuple[str, str]] = []
            fetched, folder_by_label, failures, _resolved = fetch_and_cache_gmail_targets(
                conn,
                tuple(to_fetch),
                config=config.gmail,
                client=gmail_client,
                window_start=window_start,
                fetched_at=when,
                folders_include=config.folders_include,
                folders_exclude=config.folders_exclude,
                refresh=config.gmail_refresh,
                notices=notices,
            )
            for label_name, code in notices:
                warnings.append(
                    StageWarning(
                        code=code,
                        message=f"Gmail history expired for {label_name}; full sync",
                        folder=folder_by_label.get(label_name) or folder_for.get(label_name),
                    )
                )
            for label_name, raws in fetched.items():
                _emit(label_name, folder_by_label.get(label_name, label_name), raws)
            for label_name, error in failures:
                degraded = True
                folder_name = folder_by_label.get(label_name) or folder_for.get(label_name)
                snapshot = get_label_snapshot(conn, label_name)
                if snapshot is not None and snapshot.message_ids:
                    stale = load_raw_messages(conn, snapshot.message_ids)
                    from rollup.gmail.config import folder_name_for_label

                    _emit(
                        label_name,
                        folder_name or folder_name_for_label(label_name, strip_prefix=strip),
                        stale,
                    )
                    warnings.append(
                        StageWarning(
                            code="gmail_cache_stale",
                            message=(
                                f"Using stale Gmail cache for {label_name}: "
                                f"{sanitize_provider_message(error)}"
                            ),
                            folder=folder_name,
                        )
                    )
                else:
                    warnings.append(
                        StageWarning(
                            code="gmail_fetch_failed",
                            message=f"{label_name}: {sanitize_provider_message(error)}",
                            folder=folder_name,
                        )
                    )
                    logger.warning(
                        "Gmail fetch failed for %s: %s",
                        label_name,
                        sanitize_provider_message(error),
                    )
            swept = sweep_old_messages(conn, now=when)
            if swept:
                logger.info("Gmail cache: swept %d old raw message(s)", swept)
    finally:
        conn.close()

    return messages, warnings, degraded


def merge_gmail_parse(
    parse_result: ParseResult,
    gmail_messages: list[ParsedMessage],
    gmail_warnings: list[StageWarning],
) -> ParseResult:
    """Combine prior parse results with Gmail messages."""
    return merge_parse(parse_result, gmail_messages, gmail_warnings)


def stage_parse_rss(
    config: Config,
    feeds: tuple,
    *,
    dry_run: bool,
    generated_at: datetime | None = None,
) -> tuple[list[ParsedMessage], list[StageWarning], bool]:
    """Fetch configured RSS/Atom feeds and map items to ParsedMessage."""
    if not feeds:
        return [], [], False

    from rollup.error_sanitize import sanitize_provider_message
    from rollup.rss.cache import fetch_and_cache_feeds, sweep_old_items
    from rollup.rss.parse import rss_item_to_parsed_message
    from rollup.state import init_db

    warnings: list[StageWarning] = []
    messages: list[ParsedMessage] = []
    degraded = False
    when = generated_at or datetime.now().astimezone()
    window_start, window_end = compute_date_window(when, config.lookback_days)
    conn = init_db(config.db_path)
    try:
        if dry_run:
            warnings.append(
                StageWarning(
                    code="rss_dry_run",
                    message=f"Would sync {len(feeds)} RSS feed(s)",
                    folder=config.rss.layout,
                )
            )
            return messages, warnings, False
        by_url, failures, logs = fetch_and_cache_feeds(
            conn,
            feeds,
            config=config.rss,
            refresh=config.rss_refresh,
            now=when,
            window_start=window_start,
            window_end=window_end,
        )
        for line in logs:
            logger.info(line)
        feeds_by_url = {feed.url: feed for feed in feeds}
        for url, items in by_url.items():
            feed = feeds_by_url.get(url)
            if feed is None:
                continue
            for item in items:
                try:
                    messages.append(
                        rss_item_to_parsed_message(
                            item,
                            feed=feed,
                            layout=config.rss.layout,
                            max_body_chars=config.max_body_chars,
                        )
                    )
                except Exception as exc:
                    warnings.append(
                        StageWarning(
                            code="rss_parse_error",
                            message=f"{item.item_key}: {exc}",
                            folder=feed.slug,
                        )
                    )
        for url, error in failures:
            degraded = True
            code = "rss_cache_stale" if error.startswith("rss_cache_stale") else "rss_fetch_failed"
            warnings.append(
                StageWarning(
                    code=code,
                    message=sanitize_provider_message(error),
                    folder=url,
                )
            )
        swept = sweep_old_items(conn, now=when)
        if swept:
            logger.info("RSS cache: swept %d old item(s)", swept)
    finally:
        conn.close()
    return messages, warnings, degraded


def merge_rss_parse(
    parse_result: ParseResult,
    rss_messages: list[ParsedMessage],
    rss_warnings: list[StageWarning],
) -> ParseResult:
    return merge_parse(parse_result, rss_messages, rss_warnings)


stage_parse = stage_parse_mbox
