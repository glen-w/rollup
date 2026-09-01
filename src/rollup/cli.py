"""Command-line interface for Rollup."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from rollup.config import (
    DEFAULT_EFFORT,
    DEFAULT_FINAL_REVIEW_MAX_CHANGED_CHARS_RATIO,
    DEFAULT_FINAL_REVIEW_MODE,
    DEFAULT_FINAL_REVIEW_PROFILE,
    DEFAULT_FINAL_REVIEW_PROVIDER,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_MAX_BODY_CHARS,
    DEFAULT_MAX_DISPLAY_LINKS,
    DEFAULT_OLLAMA_URL,
    DEFAULT_RUN_PROFILE,
    Config,
)
from rollup.discovery import build_inventory
from rollup.effort import (
    get_effort_preset,
    list_effort_presets,
    resolve_effort_name,
    resolve_profile_set,
)
from rollup.linkedin.config import LinkedInConfig
from rollup.paths import resolve_mail_paths
from rollup.pipeline import run_digest
from rollup.render import digest_output_stem, render_stats_block
from rollup.run_options import (
    GroupingConfig,
    default_manifest_config,
    resolve_run_options,
)
from rollup.run_profiles import (
    UnknownRunProfileError,
    list_run_profiles,
    resolve_run_profile,
)
from rollup.safety import SafetyError, validate_read_root, validate_writable_run_paths
from rollup.summary_profiles import (
    get_canonical_newsletter_types,
    list_summary_profiles as list_summary_profile_infos,
    require_valid_summary_profile_set,
)
from rollup.user_config import (
    LoadedUserConfig,
    UserConfigError,
    apply_sticky_to_namespace,
    extract_config_path,
    flag_present,
    load_user_config,
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


def _resolve_no_linkedin(args: argparse.Namespace) -> bool:
    """Default is no LinkedIn unless --linkedin is passed."""
    if getattr(args, "linkedin", False):
        return False
    return True


def _resolve_no_webpage(args: argparse.Namespace) -> bool:
    """Default is webpage ingest on unless --no-webpage is passed."""
    if getattr(args, "no_webpage", False):
        return True
    return False


def _resolve_linkedin_config(args: argparse.Namespace) -> LinkedInConfig:
    loaded = getattr(args, "_loaded_user_config", None)
    linkedin = getattr(loaded, "linkedin", None) or LinkedInConfig()
    if getattr(args, "no_linkedin_article_fetch", False):
        from dataclasses import replace

        return replace(linkedin, article_fetch=False)
    return linkedin


def _ignored_ollama_flag_warnings(config: Config) -> list[str]:
    """Warn when LLM-only flags are passed but summarisation is disabled."""
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
        f"Ignoring {flag_list} because LLM summarisation is disabled "
        f"(default; pass --ollama to enable)."
    ]


def _effort_profile_set_conflict_error(args: argparse.Namespace) -> str | None:
    """Reject combining --effort with a custom --summary-profile-set."""
    if getattr(args, "effort", None) and getattr(args, "summary_profile_set", None):
        return (
            "Cannot combine --effort with --summary-profile-set; "
            "pick one (effort selects a built-in ladder; "
            "profile-set loads a custom JSON ladder)."
        )
    return None


def _build_config(
    args: argparse.Namespace,
    *,
    folder_themes: dict | None = None,
) -> Config:
    variants_raw = getattr(args, "summary_variants", "") or ""
    summary_variants = tuple(v.strip() for v in variants_raw.split(",") if v.strip())
    summary_type_routing = getattr(args, "summary_type_routing", None)
    if summary_type_routing is None and getattr(args, "ollama", False):
        summary_type_routing = bool(
            not getattr(args, "summary_profile", None) and not summary_variants
        )

    effort = getattr(args, "effort", None)
    loaded = getattr(args, "_loaded_user_config", None)
    effort_overrides = dict(getattr(loaded, "efforts", {}) or {})
    effort_name = resolve_effort_name(effort)
    preset = get_effort_preset(
        effort_name, override=effort_overrides.get(effort_name)
    )

    raw_single = getattr(args, "single_model", None)
    single_model = (
        raw_single.strip()
        if isinstance(raw_single, str) and raw_single.strip()
        else None
    )

    ollama_model = getattr(args, "ollama_model", None)
    if ollama_model is None:
        ollama_model = single_model or preset.ollama_model

    max_chars_for_llm = getattr(args, "max_chars_for_llm", None)
    if max_chars_for_llm is None:
        max_chars_for_llm = preset.max_chars_for_llm

    final_review_model = getattr(args, "final_review_model", None)
    if final_review_model is None:
        final_review_model = single_model or preset.final_review_model

    llm_model = getattr(args, "llm_model", None)
    if llm_model is None and single_model:
        llm_model = single_model

    return Config(
        root=Path(args.root).expanduser(),
        mail_root=Path(args.mail_root).expanduser(),
        output_dir=Path(args.output_dir).expanduser(),
        state_dir=Path(args.state_dir).expanduser(),
        log_dir=Path(args.log_dir).expanduser(),
        lookback_days=getattr(args, "lookback_days", DEFAULT_LOOKBACK_DAYS),
        folders_include=tuple(getattr(args, "folder", None) or []),
        folders_exclude=tuple(getattr(args, "exclude_folder", None) or []),
        no_ollama=_resolve_no_ollama(args),
        include_seen_undated=getattr(args, "include_seen_undated", False),
        rebuild_summaries=getattr(args, "rebuild_summaries", False),
        max_body_chars=getattr(args, "max_body_chars", DEFAULT_MAX_BODY_CHARS),
        max_chars_for_llm=max_chars_for_llm,
        max_display_links=getattr(args, "max_display_links", DEFAULT_MAX_DISPLAY_LINKS),
        ollama_url=getattr(args, "ollama_url", DEFAULT_OLLAMA_URL),
        ollama_model=ollama_model,
        allow_remote_ollama=getattr(args, "allow_remote_ollama", False),
        llm_provider=getattr(args, "llm_provider", DEFAULT_LLM_PROVIDER),
        llm_model=llm_model,
        llm_api_base=getattr(args, "llm_api_base", None),
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
        final_review_model=final_review_model,
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
        effort=effort,
        list_efforts=getattr(args, "list_efforts", False),
        run_profile=getattr(args, "profile", None),
        list_profiles=getattr(args, "list_profiles", False),
        folder_themes=dict(folder_themes or {}),
        effort_overrides=effort_overrides,
        single_model=single_model,
        no_linkedin=_resolve_no_linkedin(args),
        linkedin=_resolve_linkedin_config(args),
        no_webpage=_resolve_no_webpage(args),
    )


def _resolve_profile_name(
    args: argparse.Namespace,
    loaded: LoadedUserConfig,
    argv: list[str],
) -> str:
    if flag_present(argv, "--profile"):
        return getattr(args, "profile", None) or DEFAULT_RUN_PROFILE
    if loaded.has("profile"):
        return str(loaded.get("profile"))
    return getattr(args, "profile", None) or DEFAULT_RUN_PROFILE


def _apply_loaded_config(
    args: argparse.Namespace,
    loaded: LoadedUserConfig,
    argv: list[str],
) -> str:
    """Merge TOML + run profile into args. Returns resolved profile name."""
    profile_name = _resolve_profile_name(args, loaded, argv)
    try:
        profile = resolve_run_profile(
            profile_name, toml_profiles=loaded.profiles
        )
    except UnknownRunProfileError as exc:
        raise UserConfigError(str(exc)) from exc
    args.profile = profile.name

    sticky: dict = {}
    sticky.update(loaded.values)
    sticky.pop("profile", None)
    sticky.update(profile.values)
    sticky.pop("profile", None)
    apply_sticky_to_namespace(args, sticky, argv)
    if not flag_present(argv, "--linkedin") and not flag_present(argv, "--no-linkedin"):
        if loaded.linkedin.enabled:
            args.linkedin = True
            args.no_linkedin = False
    return profile.name


def _apply_path_discovery(
    args: argparse.Namespace,
    loaded: LoadedUserConfig,
    argv: list[str],
) -> None:
    root_explicit = loaded.has("root") or flag_present(argv, "--root")
    mail_explicit = loaded.has("mail_root") or flag_present(argv, "--mail-root")
    resolved = resolve_mail_paths(
        root=Path(args.root),
        mail_root=Path(args.mail_root),
        root_explicit=root_explicit,
        mail_root_explicit=mail_explicit,
    )
    args.root = str(resolved.root)
    args.mail_root = str(resolved.mail_root)
    args._path_resolution = resolved  # noqa: SLF001


def _print_run_profile_listing(loaded: LoadedUserConfig) -> None:
    for profile in list_run_profiles(toml_profiles=loaded.profiles):
        bits = []
        if "lookback_days" in profile.values:
            bits.append(f"lookback={profile.values['lookback_days']}")
        if profile.values.get("no_grouping"):
            bits.append("grouping=off")
        else:
            bits.append("grouping=on")
        if "effort" in profile.values:
            bits.append(f"effort={profile.values['effort']}")
        detail = f" ({', '.join(bits)})" if bits else ""
        print(f"{profile.name}: {profile.description}{detail}")


def cmd_config_print(args: argparse.Namespace, loaded: LoadedUserConfig) -> int:
    """Print effective merged settings for debugging."""
    config = _build_config(args, folder_themes=loaded.folder_themes)
    profile_name = getattr(args, "profile", None) or DEFAULT_RUN_PROFILE
    try:
        profile = resolve_run_profile(
            profile_name, toml_profiles=loaded.profiles
        )
    except UnknownRunProfileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    sticky: dict = {}
    sticky.update(loaded.values)
    sticky.pop("profile", None)
    sticky.update(profile.values)

    payload = {
        "config_sources": [str(p) for p in loaded.sources],
        "run_profile": profile.name,
        "effort": getattr(args, "effort", None) or sticky.get("effort") or DEFAULT_EFFORT,
        "lookback_days": getattr(args, "lookback_days", None)
        or sticky.get("lookback_days")
        or DEFAULT_LOOKBACK_DAYS,
        "root": str(Path(args.root).expanduser()),
        "mail_root": str(Path(args.mail_root).expanduser()),
        "output_dir": str(Path(args.output_dir).expanduser()),
        "state_dir": str(Path(args.state_dir).expanduser()),
        "log_dir": str(Path(args.log_dir).expanduser()),
        "output": list(getattr(args, "output", None) or sticky.get("output") or [])
        or ["all"],
        "folder": list(getattr(args, "folder", None) or sticky.get("folder") or []),
        "exclude_folder": list(
            getattr(args, "exclude_folder", None)
            or sticky.get("exclude_folder")
            or []
        ),
        "ollama": config.llm_enabled
        if (
            getattr(args, "ollama", False)
            or getattr(args, "no_ollama", False)
            or "ollama" in sticky
        )
        else sticky.get("ollama", False),
        "llm_enabled": config.llm_enabled,
        "llm_provider": config.llm_provider,
        "llm_model": config.llm_model,
        "no_grouping": bool(
            getattr(args, "no_grouping", False)
            or sticky.get("no_grouping", False)
        ),
        "folder_themes": {
            slug: {
                "emoji": t.emoji,
                "accent": t.accent,
                "display_name": t.display_name,
                "order": t.order,
            }
            for slug, t in loaded.folder_themes.items()
        },
        "ui": {
            "landing_page": loaded.ui.landing_page,
            "preferred_view": loaded.ui.preferred_view,
            "onboarding_complete": loaded.ui.onboarding_complete,
        },
        "profiles_defined": sorted(
            {p.name for p in list_run_profiles(toml_profiles=loaded.profiles)}
        ),
    }
    path_res = getattr(args, "_path_resolution", None)
    if path_res is not None:
        payload["path_source"] = path_res.source
        if path_res.message:
            payload["path_message"] = path_res.message
    print(json.dumps(payload, indent=2))
    return 0


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
    validate_writable_run_paths(
        newsletter_root=config.root,
        mail_root=config.mail_root,
        output_dir=config.output_dir,
        state_dir=config.state_dir,
        log_dir=config.log_dir,
        db_path=config.db_path,
        extra=tuple(writable),
    )
    return warnings


def _load_and_validate_profile_set(config: Config):
    profile_set = resolve_profile_set(
        effort=config.effort,
        summary_profile_set_path=config.summary_profile_set_path,
        effort_overrides=config.effort_overrides,
        single_model=config.single_model,
        llm_provider=config.llm_provider,
    )
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


def _print_effort_listing(overrides=None) -> None:
    for preset in list_effort_presets(overrides):
        models = ", ".join(preset.expected_models())
        print(
            f"{preset.name}: {preset.description} "
            f"ollama_model={preset.ollama_model} "
            f"final_review_model={preset.final_review_model} "
            f"max_chars_for_llm={preset.max_chars_for_llm} "
            f"models=[{models}]"
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
    conflict = _effort_profile_set_conflict_error(args)
    if conflict:
        print(f"ERROR: {conflict}", file=sys.stderr)
        return 1
    loaded: LoadedUserConfig = getattr(args, "_loaded_user_config", LoadedUserConfig())
    if getattr(args, "list_efforts", False):
        _print_effort_listing(loaded.efforts)
        return 0
    if getattr(args, "list_profiles", False):
        _print_run_profile_listing(loaded)
        return 0

    config = _build_config(args, folder_themes=loaded.folder_themes)
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
    path_res = getattr(args, "_path_resolution", None)
    if path_res is not None and path_res.message and path_res.source != "discovered":
        print(f"WARNING: {path_res.message}", file=sys.stderr)

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
        if run_options.dry_run:
            logger.info(
                "Dry run — skipping summary profile set export to %s",
                config.export_summary_profile_set_path,
            )
            return 0
        from rollup.summary_profiles import export_summary_profile_set

        export_summary_profile_set(profile_set, config.export_summary_profile_set_path)
        print(
            f"Exported summary profile set to {config.export_summary_profile_set_path}"
        )
        return 0
    for warning in _ignored_ollama_flag_warnings(config):
        logger.warning(warning)

    from rollup.output_writers import (
        OutputWriterError,
        WriteContext,
        discover_writers,
        run_enabled_writers,
        validate_requested_writers,
    )

    try:
        writers = discover_writers()
    except OutputWriterError as exc:
        logger.error("%s", exc)
        return 1

    writer_err = validate_requested_writers(args, writers)
    if writer_err:
        logger.error("%s", writer_err)
        return 1

    try:
        result = run_digest(
            config,
            run_options,
            grouping=grouping,
            manifest_config=default_manifest_config(config.state_dir),
            output_writers=writers,
            writer_cli_args=args,
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
    llm_provider = getattr(args, "llm_provider", None)
    if llm_provider:
        extra.extend(["--llm-provider", str(llm_provider)])
    llm_model = getattr(args, "llm_model", None)
    if llm_model:
        extra.extend(["--llm-model", str(llm_model)])

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


from rollup.cli_parser import build_parser


def main(argv: list[str] | None = None) -> None:
    from rollup.env_file import load_rollup_env

    load_rollup_env()
    raw = list(argv) if argv is not None else sys.argv[1:]
    try:
        config_path, _ = extract_config_path(raw)
        loaded = load_user_config(explicit_path=config_path)
    except UserConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    parser = build_parser()
    args = parser.parse_args(raw)
    args._loaded_user_config = loaded  # noqa: SLF001

    try:
        if args.command in {"digest", "inventory", "doctor", "config"}:
            _apply_loaded_config(args, loaded, raw)
            _apply_path_discovery(args, loaded, raw)
        elif args.command == "cron":
            _apply_loaded_config(args, loaded, raw)
            _apply_path_discovery(args, loaded, raw)
        elif args.command == "web":
            # Sticky paths / profile defaults apply to web the same way as digest.
            _apply_loaded_config(args, loaded, raw)
            _apply_path_discovery(args, loaded, raw)
            args._config_path = config_path  # noqa: SLF001
    except UserConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    if args.command == "inventory":
        sys.exit(cmd_inventory(args))
    elif args.command == "digest":
        sys.exit(cmd_digest(args))
    elif args.command == "doctor":
        sys.exit(cmd_doctor(args))
    elif args.command == "cron":
        sys.exit(cmd_cron(args))
    elif args.command == "config":
        if getattr(args, "config_command", None) == "print":
            sys.exit(cmd_config_print(args, loaded))
        parser.parse_args(["config", "--help"])
        sys.exit(1)
    elif args.command == "sources":
        from rollup.sources_cmd import cmd_sources

        sys.exit(cmd_sources(args))
    elif args.command == "web":
        from rollup.web.cli_web import cmd_web

        sys.exit(cmd_web(args))
    elif args.command == "bodies":
        from rollup.bodies_cmd import cmd_bodies

        sys.exit(cmd_bodies(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
