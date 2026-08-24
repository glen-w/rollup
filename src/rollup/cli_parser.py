"""Argument parser construction for the Rollup CLI."""

from __future__ import annotations

import argparse

from rollup import __version__
from rollup.config import (
    DEFAULT_EFFORT,
    DEFAULT_FINAL_REVIEW_MAX_CHANGED_CHARS_RATIO,
    DEFAULT_FINAL_REVIEW_MODE,
    DEFAULT_FINAL_REVIEW_PROFILE,
    DEFAULT_FINAL_REVIEW_PROVIDER,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_LOG_DIR,
    DEFAULT_MAIL_ROOT,
    DEFAULT_MAX_BODY_CHARS,
    DEFAULT_MAX_DISPLAY_LINKS,
    DEFAULT_NEWSLETTER_ROOT,
    DEFAULT_OLLAMA_URL,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_RUN_PROFILE,
    DEFAULT_STATE_DIR,
)
from rollup.effort import EFFORT_NAMES

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rollup",
        description="Local read-only Thunderbird newsletter digest",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--config",
        help="Load settings from this TOML file (skips default config search paths)",
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
        "--profile",
        default=DEFAULT_RUN_PROFILE,
        help="Named run profile: weekly (default), daily, or a custom [profiles.*] name",
    )
    dig.add_argument(
        "--list-profiles",
        action="store_true",
        default=False,
        help="List built-in and config-defined run profiles and exit",
    )
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
        help="Enable LLM summarisation (explicit opt-in; uses local Ollama by default)",
    )
    ollama_group.add_argument(
        "--no-ollama",
        action="store_true",
        help="Disable all LLM calls (default when neither flag is passed)",
    )
    dig.add_argument("--include-seen-undated", action="store_true", default=False)
    dig.add_argument(
        "--rebuild-summaries",
        action="store_true",
        default=False,
        help="LLM only: bypass summary cache",
    )
    dig.add_argument("--max-body-chars", type=int, default=DEFAULT_MAX_BODY_CHARS)
    dig.add_argument(
        "--max-chars-for-llm",
        type=int,
        default=None,
        help="Max body chars sent to the LLM (default: from --effort)",
    )
    dig.add_argument("--max-display-links", type=int, default=DEFAULT_MAX_DISPLAY_LINKS)
    dig.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    dig.add_argument(
        "--ollama-model",
        default=None,
        help="Ollama model for group summaries / fallback (default: from --effort)",
    )
    dig.add_argument("--allow-remote-ollama", action="store_true", default=False)
    dig.add_argument(
        "--llm-provider",
        choices=["ollama", "litellm"],
        default=DEFAULT_LLM_PROVIDER,
        help="Default/fallback/group-summary LLM provider (default: ollama)",
    )
    dig.add_argument(
        "--llm-model",
        default=None,
        help="LiteLLM model for fallback/group summaries (e.g. openai/gpt-4o)",
    )
    dig.add_argument(
        "--llm-api-base",
        default=None,
        help="Optional LiteLLM api_base (CLI only; not sticky)",
    )
    dig.add_argument(
        "--effort",
        choices=list(EFFORT_NAMES),
        default=None,
        help=(
            "Machine-power preset for summary models and related defaults "
            f"(default: {DEFAULT_EFFORT}). Cannot combine with --summary-profile-set."
        ),
    )
    dig.add_argument(
        "--single-model",
        default=None,
        metavar="NAME",
        help=(
            "Use this model for every summary profile, group/fallback, and "
            "final review (this run only). Effort still controls budgets."
        ),
    )
    dig.add_argument(
        "--list-efforts",
        action="store_true",
        default=False,
        help="List built-in effort presets and exit",
    )
    dig.add_argument(
        "--summary-profile",
        help="LLM only: force one profile for every message",
    )
    dig.add_argument(
        "--summary-variants",
        help="LLM only: comma-separated profiles; one digest per profile",
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
        help="LLM only: print profile/model usage after the run",
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
        help="Max group-summary LLM calls per run",
    )
    dig.add_argument(
        "--final-review-profile",
        choices=["strict", "concise", "editorial"],
        default=DEFAULT_FINAL_REVIEW_PROFILE,
        help="Final review profile",
    )
    dig.add_argument(
        "--final-review-model",
        default=None,
        help="Override final review model (default: from --effort)",
    )
    dig.add_argument(
        "--final-review-provider",
        choices=["ollama", "litellm"],
        default=DEFAULT_FINAL_REVIEW_PROVIDER,
        help="Final review provider",
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
    dig.add_argument(
        "--output",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Output writer addon (repeatable). Default: all discovered writers "
            "(xteink, txt, json, epub, …). Pass 'none' for Markdown/HTML only."
        ),
    )
    dig.add_argument(
        "--xteink",
        "--x3",
        dest="xteink",
        action="store_true",
        default=False,
        help="Write XTEINK e-ink optimized Markdown (alias for --output xteink; "
        "selecting any --output/--xteink replaces the default-all set; "
        "--x3 remains as a compatibility alias)",
    )
    try:
        from rollup.output_writers import OutputWriterError, register_writer_cli

        register_writer_cli(dig)
    except OutputWriterError:
        pass

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
        help="Probe LLM transports even when --ollama is not set",
    )
    doc.add_argument(
        "--ollama",
        action="store_true",
        default=False,
        help="Treat LLM as enabled for network/loopback checks",
    )
    doc.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    doc.add_argument(
        "--ollama-model",
        default=None,
        help="Ollama model to probe (default: from --effort)",
    )
    doc.add_argument("--allow-remote-ollama", action="store_true", default=False)
    doc.add_argument(
        "--effort",
        choices=list(EFFORT_NAMES),
        default=None,
        help=(
            "Machine-power preset used for expected model hints "
            f"(default: {DEFAULT_EFFORT})"
        ),
    )
    doc.add_argument(
        "--single-model",
        default=None,
        metavar="NAME",
        help=(
            "Expect this one model for every summary profile and companion "
            "(same as digest --single-model)"
        ),
    )

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
            p.add_argument(
                "--llm-provider",
                choices=["ollama", "litellm"],
                default=None,
                help="Include --llm-provider in the generated command",
            )
            p.add_argument(
                "--llm-model",
                default=None,
                help="Include --llm-model in the generated command",
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

    from rollup.bodies_cmd import add_bodies_subparser

    add_bodies_subparser(sub)

    from rollup.web.cli_web import register_web_parser

    register_web_parser(sub)

    cfg = sub.add_parser(
        "config",
        help="Inspect effective configuration (TOML + profiles + defaults)",
    )
    cfg_sub = cfg.add_subparsers(dest="config_command", required=True)
    cfg_print = cfg_sub.add_parser(
        "print",
        help="Print effective merged settings as JSON",
    )
    _add_common_args(cfg_print)
    cfg_print.add_argument(
        "--profile",
        default=DEFAULT_RUN_PROFILE,
        help="Run profile to resolve (default: weekly)",
    )
    cfg_print.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    cfg_print.add_argument(
        "--effort",
        choices=list(EFFORT_NAMES),
        default=None,
        help=f"Machine-power preset (default: {DEFAULT_EFFORT})",
    )
    ollama_cfg = cfg_print.add_mutually_exclusive_group()
    ollama_cfg.add_argument("--ollama", action="store_true")
    ollama_cfg.add_argument("--no-ollama", action="store_true")
    grouping_cfg = cfg_print.add_mutually_exclusive_group()
    grouping_cfg.add_argument("--grouping", action="store_true")
    grouping_cfg.add_argument("--no-grouping", action="store_true")

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
