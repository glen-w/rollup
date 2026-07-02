"""Command-line interface for Rollup."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from rollup import __version__
from rollup.config import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_LOG_DIR,
    DEFAULT_MAIL_ROOT,
    DEFAULT_MAX_BODY_CHARS,
    DEFAULT_MAX_CHARS_FOR_LLM,
    DEFAULT_MAX_DISPLAY_LINKS,
    DEFAULT_NEWSLETTER_ROOT,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_STATE_DIR,
    Config,
    compute_date_window,
)
from rollup.discovery import build_inventory, filter_folders, iter_mbox_files
from rollup.filter import (
    apply_undated_seen_filter,
    build_digest_entries,
    count_summary_sources,
    group_dated_by_folder,
)
from rollup.models import DigestReport, DigestStats
from rollup.parse import parse_mbox_folder
from rollup.render import (
    atomic_write_digest,
    render_html,
    render_markdown,
    render_stats_block,
)
from rollup.safety import SafetyError, assert_safe_write_paths, validate_read_root
from rollup.summary_plan import SummaryCliOptions, resolve_summary_plan
from rollup.summary_profiles import (
    get_canonical_newsletter_types,
    list_summary_profiles as list_summary_profile_infos,
    load_summary_profile_set,
    require_valid_summary_profile_set,
)

logger = logging.getLogger(__name__)


def _setup_logging(
    verbose: bool, quiet: bool, log_dir: Path | None, dry_run: bool
) -> None:
    if verbose:
        level = logging.DEBUG
    elif quiet:
        level = logging.WARNING
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    if log_dir and not dry_run:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"rollup-{datetime.now().strftime('%Y-%m-%d')}.log"
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
        logging.getLogger().addHandler(fh)


def _resolve_no_ollama(args: argparse.Namespace) -> bool:
    """MVP default is no Ollama unless --ollama is passed."""
    if getattr(args, "ollama", False):
        return False
    return True


def _ignored_ollama_flag_warnings(config: Config) -> list[str]:
    """Warn when Ollama-only flags are passed but summarisation is disabled."""
    if not config.no_ollama:
        return []

    ignored: list[str] = []
    if config.summary_profile:
        ignored.append("--summary-profile")
    if config.summary_variants:
        ignored.append("--summary-variants")
    if config.rebuild_summaries:
        ignored.append("--rebuild-summaries")
    if config.summary_routing_report:
        ignored.append("--summary-routing-report")
    if config.summary_type_routing is True:
        ignored.append("--summary-type-routing")
    if config.summary_type_routing is False:
        ignored.append("--no-summary-type-routing")
    if config.allow_remote_ollama:
        ignored.append("--allow-remote-ollama")
    if not ignored:
        return []
    flag_list = ", ".join(ignored)
    return [
        f"Ignoring {flag_list} because Ollama summarisation is disabled "
        f"(default; pass --ollama to enable)."
    ]


def _build_config(args: argparse.Namespace) -> Config:
    variants_raw = getattr(args, "summary_variants", "") or ""
    summary_variants = tuple(v.strip() for v in variants_raw.split(",") if v.strip())
    summary_type_routing = getattr(args, "summary_type_routing", None)
    if summary_type_routing is None and getattr(args, "ollama", False):
        summary_type_routing = bool(
            not getattr(args, "summary_profile", None)
            and not summary_variants
        )
    return Config(
        root=Path(args.root),
        mail_root=Path(args.mail_root),
        output_dir=Path(args.output_dir),
        state_dir=Path(args.state_dir),
        log_dir=Path(args.log_dir),
        lookback_days=getattr(args, "lookback_days", DEFAULT_LOOKBACK_DAYS),
        folders_include=tuple(getattr(args, "folder", None) or []),
        folders_exclude=tuple(getattr(args, "exclude_folder", None) or []),
        dry_run=getattr(args, "dry_run", False),
        no_ollama=_resolve_no_ollama(args),
        include_seen_undated=getattr(args, "include_seen_undated", False),
        rebuild_summaries=getattr(args, "rebuild_summaries", False),
        max_body_chars=getattr(args, "max_body_chars", DEFAULT_MAX_BODY_CHARS),
        max_chars_for_llm=getattr(args, "max_chars_for_llm", DEFAULT_MAX_CHARS_FOR_LLM),
        max_display_links=getattr(args, "max_display_links", DEFAULT_MAX_DISPLAY_LINKS),
        ollama_url=getattr(args, "ollama_url", DEFAULT_OLLAMA_URL),
        ollama_model=getattr(args, "ollama_model", DEFAULT_OLLAMA_MODEL),
        allow_remote_ollama=getattr(args, "allow_remote_ollama", False),
        summary_profile=getattr(args, "summary_profile", None),
        summary_variants=summary_variants,
        summary_type_routing=summary_type_routing,
        summary_profile_set_path=getattr(args, "summary_profile_set", None),
        export_summary_profile_set_path=getattr(
            args, "export_summary_profile_set", None
        ),
        list_summary_profiles=getattr(args, "list_summary_profiles", False),
        list_newsletter_types=getattr(args, "list_newsletter_types", False),
        summary_routing_report=getattr(args, "summary_routing_report", False),
        verbose=getattr(args, "verbose", False),
        quiet=getattr(args, "quiet", False),
    )


def _validate_config(config: Config, json_out: Path | None = None) -> list[str]:
    warnings = validate_read_root(
        config.root,
        config.mail_root,
        config.output_dir,
        config.state_dir,
        config.log_dir,
    )
    writable = [
        config.output_dir,
        config.state_dir,
        config.log_dir,
        config.db_path,
    ]
    if json_out:
        writable.append(json_out)
    digest_date = datetime.now().astimezone().strftime("%Y-%m-%d")
    writable.extend(
        [
            config.output_dir / f"{digest_date}-newsletter-digest.md",
            config.output_dir / f"{digest_date}-newsletter-digest.html",
            config.output_dir / f".tmp-{digest_date}-newsletter-digest.md",
            config.output_dir / f".tmp-{digest_date}-newsletter-digest.html",
        ]
    )
    for variant in config.summary_variants:
        writable.extend(
            [
                config.output_dir / f"{digest_date}-newsletter-digest.{variant}.md",
                config.output_dir / f"{digest_date}-newsletter-digest.{variant}.html",
                config.output_dir
                / f".tmp-{digest_date}-newsletter-digest.{variant}.md",
                config.output_dir
                / f".tmp-{digest_date}-newsletter-digest.{variant}.html",
            ]
        )
    if config.export_summary_profile_set_path:
        writable.append(Path(config.export_summary_profile_set_path))
    assert_safe_write_paths(config.mail_root, *writable)
    return warnings


def _load_and_validate_profile_set(config: Config):
    profile_set = load_summary_profile_set(config.summary_profile_set_path)
    return require_valid_summary_profile_set(
        profile_set, get_canonical_newsletter_types()
    )


def _print_summary_profile_listing(profile_set) -> None:
    for info in list_summary_profile_infos(profile_set):
        print(
            f"{info.name}: provider={info.provider} model={info.model} "
            f"prompt_style={info.prompt_style} temperature={info.temperature}"
        )


def _print_newsletter_types() -> None:
    for newsletter_type in get_canonical_newsletter_types():
        print(newsletter_type)


def _print_routing_report(report) -> None:
    if report.mode == "variants":
        print(f"Summary variants: {', '.join(report.output_variants)}")
    else:
        print(f"Summary routing mode: {report.mode}")
    if report.profiles_used:
        print(f"Profiles used: {', '.join(report.profiles_used)}")
    if report.models_used:
        print(f"Models used: {', '.join(report.models_used)}")
    for row in report.routing_counts:
        print(
            f"{row.newsletter_type}: profile={row.profile_name} "
            f"model={row.model} count={row.count}"
        )


def _build_digest_report(
    *,
    generated_at: datetime,
    lookback_days: int,
    window_start: datetime,
    window_end: datetime,
    dated_entries,
    undated_entries,
    stats: DigestStats,
    summary_metadata,
) -> DigestReport:
    return DigestReport(
        generated_at=generated_at,
        lookback_days=lookback_days,
        window_start=window_start,
        window_end=window_end,
        dated_by_folder=group_dated_by_folder(dated_entries),
        undated=tuple(undated_entries),
        stats=stats,
        summary_metadata=summary_metadata,
    )


def cmd_inventory(args: argparse.Namespace) -> int:
    config = _build_config(args)
    json_out = Path(args.json_out) if args.json_out else None
    try:
        warnings = _validate_config(config, json_out)
    except SafetyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for w in warnings:
        print(w, file=sys.stderr)

    _setup_logging(config.verbose, config.quiet, None, dry_run=True)
    logger.info("Reading newsletter root: %s", config.root.resolve())

    inventory = build_inventory(config.root)
    rows = []
    for entry in inventory:
        folder = entry.folder
        size_kb = folder.size_bytes / 1024
        count = entry.message_count if entry.message_count is not None else "?"
        err = entry.parse_error or ""
        print(
            f"{folder.folder_name:20} {str(folder.mbox_path):50} "
            f"{size_kb:8.1f} KB  msgs={count}  {err}"
        )
        rows.append(
            {
                "folder_name": folder.folder_name,
                "mbox_path": str(folder.mbox_path),
                "size_bytes": folder.size_bytes,
                "message_count": entry.message_count,
                "parse_error": entry.parse_error,
            }
        )

    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        logger.info("Wrote inventory JSON to %s", json_out)
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    config = _build_config(args)
    try:
        warnings = _validate_config(config)
    except SafetyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for w in warnings:
        print(w, file=sys.stderr)

    _setup_logging(
        config.verbose,
        config.quiet,
        config.log_dir if not config.dry_run else None,
        config.dry_run,
    )
    profile_set = _load_and_validate_profile_set(config)
    if config.list_newsletter_types:
        _print_newsletter_types()
        return 0
    if config.list_summary_profiles:
        _print_summary_profile_listing(profile_set)
        return 0
    if config.export_summary_profile_set_path:
        from rollup.summary_profiles import export_summary_profile_set

        export_summary_profile_set(profile_set, config.export_summary_profile_set_path)
        print(
            f"Exported summary profile set to {config.export_summary_profile_set_path}"
        )
        return 0
    for warning in _ignored_ollama_flag_warnings(config):
        logger.warning(warning)
    generated_at = datetime.now().astimezone()
    window_start, window_end = compute_date_window(generated_at, config.lookback_days)

    folders = list(iter_mbox_files(config.root))
    folders = filter_folders(folders, config.folders_include, config.folders_exclude)
    logger.info(
        "Digest: root=%s folders=%d lookback=%dd dry_run=%s no_ollama=%s",
        config.root,
        len(folders),
        config.lookback_days,
        config.dry_run,
        config.no_ollama,
    )

    all_messages = []
    parse_errors = 0
    for folder in folders:
        logger.info("Parsing %s (%s)", folder.folder_name, folder.mbox_path)
        msgs, errors, folder_errors = parse_mbox_folder(
            folder, config.max_body_chars, config.max_display_links
        )
        if folder_errors:
            logger.error("Folder %s: %s", folder.folder_name, folder_errors[0])
            parse_errors += 1
            continue
        parse_errors += errors
        all_messages.extend(msgs)

    dated_entries, undated_entries, skipped_window, deduped = build_digest_entries(
        all_messages, generated_at, config.lookback_days, config.no_ollama
    )

    if not config.no_ollama and not config.dry_run:
        from rollup.summarize import OllamaError, validate_ollama_url

        try:
            validate_ollama_url(config.ollama_url, config.allow_remote_ollama)
        except OllamaError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    seen_keys: set[str] = set()
    conn = None
    if not config.dry_run:
        from rollup.state import init_db, load_seen_keys

        if not config.no_ollama:
            from rollup.state import init_db_with_summaries

            conn = init_db_with_summaries(config.db_path)
        else:
            conn = init_db(config.db_path)
        seen_keys = load_seen_keys(conn)

    undated_to_render, skipped_seen = apply_undated_seen_filter(
        undated_entries, seen_keys, config.include_seen_undated
    )

    summary_metadata = None
    rendered_variants: dict[str, tuple[list, list]] = {}
    if not config.no_ollama and not config.dry_run:
        from rollup.summarize import execute_summary_plan

        routing = config.summary_type_routing
        if routing is None:
            routing = not config.summary_profile and not config.summary_variants
        cli_options = SummaryCliOptions(
            summary_profile=config.summary_profile,
            summary_variants=config.summary_variants,
            summary_type_routing=routing,
        )
        all_entries = dated_entries + undated_to_render
        plan = resolve_summary_plan(all_entries, profile_set, cli_options)
        execution = execute_summary_plan(
            entries=all_entries,
            plan=plan,
            ollama_url=config.ollama_url,
            default_model=config.ollama_model,
            max_chars=config.max_chars_for_llm,
            allow_remote=config.allow_remote_ollama,
            conn=conn,
            rebuild=config.rebuild_summaries,
            quiet=config.quiet,
        )
        dated_count = len(dated_entries)
        for variant_name, rendered in execution.entries_by_variant.items():
            rendered_variants[variant_name] = (
                rendered[:dated_count],
                rendered[dated_count:],
            )
        default_variant_name = (
            "default"
            if "default" in rendered_variants
            else next(iter(rendered_variants))
        )
        dated_entries, undated_to_render = rendered_variants[default_variant_name]
        summary_metadata = execution.summary_metadata_by_variant.get(
            default_variant_name
        )

    all_rendered = dated_entries + undated_to_render
    ollama_c, cache_c, fallback_c = count_summary_sources(all_rendered)

    stats = DigestStats(
        folders_scanned=len(folders),
        messages_parsed=len(all_messages),
        dated_included=len(dated_entries),
        undated_needing_review=len(undated_to_render),
        skipped_outside_window=skipped_window,
        skipped_seen_undated=skipped_seen,
        deduped_messages=deduped,
        parse_errors=parse_errors,
        summaries_ollama=ollama_c,
        summaries_cache=cache_c,
        summaries_fallback=fallback_c,
        summaries_errors=summary_metadata.summaries_errors if summary_metadata else 0,
    )

    report = _build_digest_report(
        generated_at=generated_at,
        lookback_days=config.lookback_days,
        window_start=window_start,
        window_end=window_end,
        dated_entries=dated_entries,
        undated_entries=undated_to_render,
        stats=stats,
        summary_metadata=summary_metadata,
    )

    print(render_stats_block(stats))
    if summary_metadata and config.summary_routing_report:
        _print_routing_report(summary_metadata)

    if config.dry_run:
        logger.info("Dry run — no files written, no state updated")
        return 0

    try:
        if rendered_variants and any(name != "default" for name in rendered_variants):
            for variant_name, (
                variant_dated,
                variant_undated,
            ) in rendered_variants.items():
                variant_metadata = execution.summary_metadata_by_variant.get(
                    variant_name
                )
                variant_stats = DigestStats(
                    folders_scanned=stats.folders_scanned,
                    messages_parsed=stats.messages_parsed,
                    dated_included=len(variant_dated),
                    undated_needing_review=len(variant_undated),
                    skipped_outside_window=stats.skipped_outside_window,
                    skipped_seen_undated=stats.skipped_seen_undated,
                    deduped_messages=stats.deduped_messages,
                    parse_errors=stats.parse_errors,
                    summaries_ollama=(
                        variant_metadata.summaries_ollama if variant_metadata else 0
                    ),
                    summaries_cache=(
                        variant_metadata.summaries_cache if variant_metadata else 0
                    ),
                    summaries_fallback=(
                        variant_metadata.summaries_fallback if variant_metadata else 0
                    ),
                    summaries_errors=(
                        variant_metadata.summaries_errors if variant_metadata else 0
                    ),
                )
                variant_report = _build_digest_report(
                    generated_at=generated_at,
                    lookback_days=config.lookback_days,
                    window_start=window_start,
                    window_end=window_end,
                    dated_entries=variant_dated,
                    undated_entries=variant_undated,
                    stats=variant_stats,
                    summary_metadata=variant_metadata,
                )
                md = render_markdown(variant_report, config.max_display_links)
                html_content = render_html(variant_report, config.max_display_links)
                md_path, html_path = atomic_write_digest(
                    config.output_dir,
                    generated_at,
                    md,
                    html_content,
                    variant_name=variant_name,
                )
                logger.info("Wrote %s", md_path)
                logger.info("Wrote %s", html_path)
        else:
            md = render_markdown(report, config.max_display_links)
            html_content = render_html(report, config.max_display_links)
            md_path, html_path = atomic_write_digest(
                config.output_dir, generated_at, md, html_content
            )
            logger.info("Wrote %s", md_path)
            logger.info("Wrote %s", html_path)

        if conn is not None:
            from rollup.state import upsert_seen_keys

            rendered_undated_keys = [
                e.classified.parsed.message_key for e in undated_to_render
            ]
            upsert_seen_keys(conn, rendered_undated_keys, generated_at)
            conn.close()
    except Exception as exc:
        logger.error("Digest write failed: %s", exc)
        if conn is not None:
            conn.close()
        return 1

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rollup",
        description="Local read-only Thunderbird newsletter digest",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser(
        "inventory",
        help="Discover mbox folders and message counts (read-only; no mail writes)",
    )
    _add_common_args(inv)
    inv.add_argument("--json-out", help="Write inventory JSON to this path (optional)")

    dig = sub.add_parser(
        "digest",
        help="Generate weekly newsletter digest (read-only mail; writes output outside mail root)",
    )
    _add_common_args(dig)
    dig.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    dig.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Parse and report only; no output files, state DB, logs, or network/Ollama calls",
    )
    ollama_group = dig.add_mutually_exclusive_group()
    ollama_group.add_argument(
        "--ollama",
        action="store_true",
        help="Enable local Ollama summarisation (explicit opt-in; local loopback only by default)",
    )
    ollama_group.add_argument(
        "--no-ollama",
        action="store_true",
        help="Skip Ollama summarisation (default when neither flag is passed)",
    )
    dig.add_argument("--include-seen-undated", action="store_true", default=False)
    dig.add_argument(
        "--rebuild-summaries",
        action="store_true",
        default=False,
        help="Ollama only: bypass summary cache",
    )
    dig.add_argument("--max-body-chars", type=int, default=DEFAULT_MAX_BODY_CHARS)
    dig.add_argument("--max-chars-for-llm", type=int, default=DEFAULT_MAX_CHARS_FOR_LLM)
    dig.add_argument("--max-display-links", type=int, default=DEFAULT_MAX_DISPLAY_LINKS)
    dig.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    dig.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
    dig.add_argument("--allow-remote-ollama", action="store_true", default=False)
    dig.add_argument(
        "--summary-profile",
        help="Ollama only: force one profile for every message",
    )
    dig.add_argument(
        "--summary-variants",
        help="Ollama only: comma-separated profiles; one digest per profile",
    )
    dig.add_argument(
        "--summary-profile-set",
        help="Load summary profiles/routes from JSON (used with --ollama)",
    )
    dig.add_argument("--export-summary-profile-set")
    dig.add_argument("--list-summary-profiles", action="store_true", default=False)
    dig.add_argument("--list-newsletter-types", action="store_true", default=False)
    dig.add_argument(
        "--summary-routing-report",
        action="store_true",
        default=False,
        help="Ollama only: print profile/model usage after the run",
    )
    type_routing_group = dig.add_mutually_exclusive_group()
    type_routing_group.add_argument(
        "--summary-type-routing",
        dest="summary_type_routing",
        action="store_true",
        default=None,
    )
    type_routing_group.add_argument(
        "--no-summary-type-routing",
        dest="summary_type_routing",
        action="store_false",
    )

    return parser


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", default=str(DEFAULT_NEWSLETTER_ROOT))
    p.add_argument("--mail-root", default=str(DEFAULT_MAIL_ROOT))
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    p.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    p.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    p.add_argument(
        "--folder", action="append", help="Include only this folder (repeatable)"
    )
    p.add_argument(
        "--exclude-folder", action="append", help="Exclude folder (repeatable)"
    )
    p.add_argument("--verbose", action="store_true", default=False)
    p.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress INFO progress output (warnings and errors still shown)",
    )


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "inventory":
        sys.exit(cmd_inventory(args))
    elif args.command == "digest":
        sys.exit(cmd_digest(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
