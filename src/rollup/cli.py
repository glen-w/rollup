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
    DEFAULT_FINAL_REVIEW_MAX_CHANGED_CHARS_RATIO,
    DEFAULT_FINAL_REVIEW_MODE,
    DEFAULT_FINAL_REVIEW_PROFILE,
    DEFAULT_FINAL_REVIEW_PROVIDER,
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
)
from rollup.discovery import build_inventory
from rollup.pipeline import run_digest
from rollup.render import digest_output_stem, render_stats_block
from rollup.run_options import (
    GroupingConfig,
    default_manifest_config,
    resolve_run_options,
)
from rollup.safety import SafetyError, assert_safe_write_paths, validate_read_root
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
            not getattr(args, "summary_profile", None) and not summary_variants
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
        final_review_enabled=getattr(args, "final_review", False),
        final_review_mode=getattr(args, "final_review_mode", DEFAULT_FINAL_REVIEW_MODE),
        final_review_profile=getattr(
            args, "final_review_profile", DEFAULT_FINAL_REVIEW_PROFILE
        ),
        final_review_provider=getattr(
            args, "final_review_provider", DEFAULT_FINAL_REVIEW_PROVIDER
        ),
        final_review_model=getattr(args, "final_review_model", None),
        final_review_report_path=(
            Path(args.final_review_report)
            if getattr(args, "final_review_report", None)
            else None
        ),
        rebuild_final_review=getattr(args, "no_final_review_cache", False),
        final_review_preserve_links=True,
        final_review_preserve_quotes=True,
        final_review_max_changed_chars_ratio=getattr(
            args,
            "final_review_max_changed_chars_ratio",
            DEFAULT_FINAL_REVIEW_MAX_CHANGED_CHARS_RATIO,
        ),
        final_review_allow_cron_apply=getattr(
            args, "final_review_allow_cron_apply", False
        ),
        final_review_apply_policy=getattr(
            args, "final_review_apply_policy", "conservative"
        ),
        final_review_max_patches_unattended=getattr(
            args, "final_review_max_patches_unattended", 5
        ),
        final_review_max_changed_chars_unattended=getattr(
            args, "final_review_max_changed_chars_unattended", 800
        ),
        group_summaries_enabled=getattr(args, "group_summaries", False),
        max_group_summary_calls=getattr(args, "max_group_summary_calls", 8),
        group_summary_variant_policy=getattr(
            args, "group_summary_variant_policy", "primary"
        ),
        min_usable_member_summaries=getattr(args, "min_usable_member_summaries", 2),
    )


def _build_run_options(args: argparse.Namespace):
    cron = getattr(args, "cron", False)
    # Detect whether quiet was explicitly passed via argparse store_true —
    # if cron and not verbose and not --quiet, quiet comes from cron default.
    quiet_arg = True if getattr(args, "quiet", False) else (None if cron else False)
    if getattr(args, "verbose", False):
        quiet_arg = False
    elif getattr(args, "quiet", False):
        quiet_arg = True

    publish_latest = None
    if getattr(args, "latest", False) or getattr(args, "publish_latest", False):
        publish_latest = True
    elif getattr(args, "no_latest", False):
        publish_latest = False

    return resolve_run_options(
        dry_run=getattr(args, "dry_run", False),
        cron=cron,
        quiet=quiet_arg,
        verbose=getattr(args, "verbose", False),
        write_manifest=None,
        publish_latest=publish_latest,
        allow_partial_latest=getattr(args, "allow_partial_latest", False),
        no_manifest=getattr(args, "no_manifest", False),
    )


def _build_grouping_config(args: argparse.Namespace) -> GroupingConfig:
    if getattr(args, "no_grouping", False):
        enabled = False
    elif getattr(args, "grouping", False):
        enabled = True
    else:
        enabled = True  # default on
    return GroupingConfig(
        enabled=enabled,
        min_group_size=getattr(args, "grouping_min_size", 3),
        report=getattr(args, "grouping_report", False),
    )


def _validate_config(
    config: Config,
    json_out: Path | None = None,
    generated_at: datetime | None = None,
) -> list[str]:
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
        config.state_dir / "manifests",
        config.state_dir / "rollup.lock",
        config.output_dir / "latest.md",
        config.output_dir / "latest.html",
    ]
    if json_out:
        writable.append(json_out)
    digest_at = generated_at or datetime.now().astimezone()
    for variant in (None, *config.summary_variants):
        stem = digest_output_stem(digest_at, variant, run_id_short="preview")
        writable.extend(
            [
                config.output_dir / f"{stem}.md",
                config.output_dir / f"{stem}.html",
                config.output_dir / f".tmp-{stem}.md",
                config.output_dir / f".tmp-{stem}.html",
            ]
        )
        if config.final_review_enabled:
            review_path = (
                config.final_review_report_path if variant is None else None
            )
            if review_path is None:
                writable.append(config.output_dir / f"{stem}.final-review.json")
    if config.final_review_report_path:
        writable.append(config.final_review_report_path)
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
            f"prompt_style={info.prompt_style} temperature={info.temperature} "
            f"num_predict={info.num_predict} think={info.think}"
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
    for row in getattr(report, "anomaly_rows", ()):
        elapsed = (
            f"{row.elapsed_seconds:.1f}s"
            if row.elapsed_seconds is not None
            else "n/a"
        )
        stop_reason = row.stop_reason or "n/a"
        print(
            f'{row.status}: subject="{row.subject}" profile={row.profile_name} '
            f"stop_reason={stop_reason} output_chars={row.output_chars} "
            f"elapsed={elapsed} cached={str(row.cached).lower()}"
        )


def _validate_final_review_config(
    config: Config,
    *,
    cron: bool = False,
    dry_run: bool = False,
    grouping_enabled: bool = True,
) -> None:
    from rollup.phase3_validate import validate_phase3_runtime_config
    from rollup.run_options import GroupingConfig, RunOptions

    validate_phase3_runtime_config(
        config,
        run_options=RunOptions(cron=cron, dry_run=dry_run),
        grouping=GroupingConfig(enabled=grouping_enabled),
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

    _setup_logging(
        getattr(args, "verbose", False),
        getattr(args, "quiet", False),
        None,
        dry_run=True,
    )
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
    run_options = _build_run_options(args)
    grouping = _build_grouping_config(args)
    generated_at = datetime.now().astimezone()
    try:
        warnings = _validate_config(config, generated_at=generated_at)
    except SafetyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for w in warnings:
        print(w, file=sys.stderr)

    _setup_logging(
        run_options.verbose,
        run_options.quiet,
        config.log_dir if not run_options.dry_run else None,
        run_options.dry_run,
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

    try:
        result = run_digest(
            config,
            run_options,
            grouping=grouping,
            manifest_config=default_manifest_config(config.state_dir),
        )
    except Exception as exc:
        # Effective-run validation (Phase-3) and unexpected hard errors.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if result.stats is not None and not run_options.quiet:
        print(render_stats_block(result.stats))
    elif result.stats is not None and run_options.cron:
        # Cron: still print a one-line status to stderr via logger at WARNING+ only.
        logger.warning(
            "Digest %s: included=%d parse_errors=%d",
            result.status,
            result.stats.dated_included + result.stats.undated_needing_review,
            result.stats.parse_errors,
        )

    if (
        result.report
        and result.report.summary_metadata
        and config.summary_routing_report
    ):
        _print_routing_report(result.report.summary_metadata)

    if grouping.report and result.aggregated.grouping is not None:
        from rollup.grouping import GroupingApplyResult, build_grouping_report

        gr = result.aggregated.grouping
        print(
            build_grouping_report(
                GroupingApplyResult(
                    dated_items=gr.dated_items,
                    undated_items=gr.undated_items,
                    groups=gr.groups,
                    reason_codes=gr.reason_codes,
                )
            )
        )

    if result.error_message:
        print(f"ERROR: {result.error_message}", file=sys.stderr)
    if result.secondary_manifest_error:
        print(
            f"ERROR: Secondary manifest write failed: {result.secondary_manifest_error}",
            file=sys.stderr,
        )
    if result.md_path and not run_options.quiet:
        logger.info("Wrote %s", result.md_path)
        logger.info("Wrote %s", result.html_path)
    if run_options.dry_run:
        logger.info("Dry run — no files written, no state updated")

    return result.exit_code


def cmd_doctor(args: argparse.Namespace) -> int:
    from rollup.doctor import format_doctor_human, format_doctor_json, run_doctor

    config = _build_config(args)
    run_options = resolve_run_options(
        dry_run=True,
        cron=False,
        quiet=getattr(args, "quiet", False),
        verbose=getattr(args, "verbose", False),
    )
    # Doctor may enable ollama checks via --ollama on the doctor command.
    report = run_doctor(
        config,
        run_options,
        full=getattr(args, "full", False),
        network=getattr(args, "network", False),
    )
    if getattr(args, "json", False):
        sys.stdout.write(format_doctor_json(report))
    else:
        print(format_doctor_human(report))
    return 0 if report.ok else 1


def cmd_cron(args: argparse.Namespace) -> int:
    from rollup.cron_helpers import (
        SchedulerPaths,
        format_cron_status,
        render_crontab,
        render_launchd_plist,
        resolve_python,
    )

    sub = args.cron_command
    if sub == "status":
        print(format_cron_status(Path(args.state_dir)))
        return 0

    python_path, warnings = resolve_python(getattr(args, "python", None))
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)

    workdir = Path(getattr(args, "workdir", ".")).expanduser().resolve()
    paths = SchedulerPaths(
        python=python_path,
        workdir=workdir,
        root=Path(args.root).expanduser().resolve(),
        mail_root=Path(args.mail_root).expanduser().resolve(),
        output_dir=Path(args.output_dir).expanduser().resolve(),
        state_dir=Path(args.state_dir).expanduser().resolve(),
        log_dir=Path(args.log_dir).expanduser().resolve(),
    )
    extra = []
    if getattr(args, "ollama", False):
        extra.append("--ollama")

    if sub == "print-crontab":
        schedule = getattr(args, "cron_schedule", "0 8 * * 0")
        sys.stdout.write(render_crontab(paths, schedule=schedule, extra=extra or None))
        return 0

    if sub == "print-launchd":
        plist = render_launchd_plist(
            paths,
            weekday=getattr(args, "weekday", 0),
            hour=getattr(args, "hour", 8),
            minute=getattr(args, "minute", 0),
            extra=extra or None,
        )
        sys.stdout.buffer.write(plist)
        return 0

    print(f"Unknown cron command: {sub}", file=sys.stderr)
    return 1


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
    dig.add_argument(
        "--cron",
        action="store_true",
        default=False,
        help="Unattended mode: quieter logs, publish latest outputs, mode=cron",
    )
    dig.add_argument(
        "--latest",
        action="store_true",
        default=False,
        help="Publish output/latest.md and latest.html after a successful run",
    )
    dig.add_argument(
        "--no-latest",
        action="store_true",
        default=False,
        help="Do not publish latest.* even in --cron mode",
    )
    dig.add_argument(
        "--allow-partial-latest",
        action="store_true",
        default=False,
        help="Allow partial runs to update latest.* (default: only success)",
    )
    dig.add_argument(
        "--no-manifest",
        action="store_true",
        default=False,
        help="Skip writing a run manifest",
    )
    grouping_group = dig.add_mutually_exclusive_group()
    grouping_group.add_argument(
        "--grouping",
        action="store_true",
        default=False,
        help="Enable deterministic grouping (default)",
    )
    grouping_group.add_argument(
        "--no-grouping",
        action="store_true",
        default=False,
        help="Disable grouping; one card per message",
    )
    dig.add_argument(
        "--grouping-report",
        action="store_true",
        default=False,
        help="Print grouping decisions to stdout",
    )
    dig.add_argument(
        "--grouping-min-size",
        type=int,
        default=3,
        help="Minimum messages to form a notification_stream group",
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
    dig.add_argument(
        "--final-review",
        action="store_true",
        default=False,
        help="Run whole-digest editorial QA review and write JSON sidecar report",
    )
    dig.add_argument(
        "--final-review-mode",
        choices=["report", "apply"],
        default=DEFAULT_FINAL_REVIEW_MODE,
        help="Final review mode: report (default) or apply safe summary patches",
    )
    dig.add_argument(
        "--final-review-allow-cron-apply",
        action="store_true",
        default=False,
        help="Allow --final-review-mode apply under --cron (fail closed without this)",
    )
    dig.add_argument(
        "--final-review-apply-policy",
        choices=["conservative", "standard"],
        default="conservative",
        help="Apply policy (cron supports conservative only)",
    )
    dig.add_argument(
        "--final-review-max-changed-chars-ratio",
        type=float,
        default=DEFAULT_FINAL_REVIEW_MAX_CHANGED_CHARS_RATIO,
        help="Max per-entry summary change ratio for apply mode",
    )
    dig.add_argument(
        "--group-summaries",
        action="store_true",
        default=False,
        help="Opt-in group-level LLM summaries (requires --ollama and grouping)",
    )
    dig.add_argument(
        "--max-group-summary-calls",
        type=int,
        default=8,
        help="Max Ollama group-summary calls per run",
    )
    dig.add_argument(
        "--final-review-profile",
        choices=["strict", "concise", "editorial"],
        default=DEFAULT_FINAL_REVIEW_PROFILE,
        help="Final review profile",
    )
    dig.add_argument(
        "--final-review-model",
        help="Override final review Ollama model",
    )
    dig.add_argument(
        "--final-review-provider",
        default=DEFAULT_FINAL_REVIEW_PROVIDER,
        help="Final review provider (ollama only)",
    )
    dig.add_argument(
        "--final-review-report",
        help="Write final review JSON report to this path",
    )
    dig.add_argument(
        "--no-final-review-cache",
        action="store_true",
        default=False,
        help="Bypass final review cache",
    )

    doc = sub.add_parser(
        "doctor",
        help="Inspect local setup, safety, and configuration",
    )
    _add_common_args(doc)
    doc.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Write JSON report to stdout (diagnostics stay on stderr)",
    )
    doc.add_argument(
        "--full",
        action="store_true",
        default=False,
        help="Run expensive read-only mbox sampling checks",
    )
    doc.add_argument(
        "--network",
        action="store_true",
        default=False,
        help="Probe Ollama even when --ollama is not set",
    )
    doc.add_argument(
        "--ollama",
        action="store_true",
        default=False,
        help="Treat Ollama as enabled for network/loopback checks",
    )
    doc.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    doc.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
    doc.add_argument("--allow-remote-ollama", action="store_true", default=False)

    cron = sub.add_parser(
        "cron",
        help="Scheduler helpers (print-launchd, print-crontab, status)",
    )
    cron_sub = cron.add_subparsers(dest="cron_command", required=True)
    for name, help_text in (
        ("print-launchd", "Print a macOS launchd LaunchAgent plist (preferred)"),
        ("print-crontab", "Print a crontab line (alternative to launchd)"),
        ("status", "Show last run status from manifests/latest.json"),
    ):
        p = cron_sub.add_parser(name, help=help_text)
        _add_common_args(p)
        if name != "status":
            p.add_argument(
                "--python",
                help="Absolute path to Python interpreter (recommended)",
            )
            p.add_argument(
                "--workdir",
                default=".",
                help="WorkingDirectory for the scheduled job",
            )
            p.add_argument(
                "--ollama",
                action="store_true",
                default=False,
                help="Include --ollama in the generated command",
            )
        if name == "print-crontab":
            p.add_argument(
                "--cron-schedule",
                default="0 8 * * 0",
                help="Crontab schedule expression (default: Sundays 08:00)",
            )
        if name == "print-launchd":
            p.add_argument("--weekday", type=int, default=0, help="0=Sunday … 6=Saturday")
            p.add_argument("--hour", type=int, default=8)
            p.add_argument("--minute", type=int, default=0)

    from rollup.sources_cmd import add_sources_subparser

    add_sources_subparser(sub)

    from rollup.web.cli_web import register_web_parser

    register_web_parser(sub)

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
    elif args.command == "doctor":
        sys.exit(cmd_doctor(args))
    elif args.command == "cron":
        sys.exit(cmd_cron(args))
    elif args.command == "sources":
        from rollup.sources_cmd import cmd_sources

        sys.exit(cmd_sources(args))
    elif args.command == "web":
        from rollup.web.cli_web import cmd_web

        sys.exit(cmd_web(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
