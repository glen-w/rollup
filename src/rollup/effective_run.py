"""Resolved runtime configuration for one digest invocation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rollup.config import Config
from rollup.final_review_codes import (
    DIGEST_BUDGET_MAX_DELTA_CHARS,
    ApplyPolicy,
    resolve_apply_policy,
)
from rollup.final_review_profiles import (
    FinalReviewConfigError,
    validate_final_review_config,
    validate_phase3_final_review_config,
)
from rollup.run_options import GroupingConfig, RunOptions


@dataclass(frozen=True)
class EffectiveRun:
    """Flat resolved config for the pipeline; no nested Config/RunOptions objects."""

    root: Path
    mail_root: Path
    output_dir: Path
    state_dir: Path
    log_dir: Path
    lookback_days: int
    folders_include: tuple[str, ...]
    folders_exclude: tuple[str, ...]
    no_ollama: bool
    include_seen_undated: bool
    rebuild_summaries: bool
    max_body_chars: int
    max_chars_for_llm: int
    max_display_links: int
    ollama_url: str
    ollama_model: str
    allow_remote_ollama: bool
    summary_profile: str | None
    summary_variants: tuple[str, ...]
    summary_type_routing: bool | None
    summary_profile_set_path: str | None
    export_summary_profile_set_path: str | None
    list_summary_profiles: bool
    list_newsletter_types: bool
    summary_routing_report: bool
    final_review_enabled: bool
    final_review_mode: str
    final_review_profile: str
    final_review_provider: str
    final_review_model: str | None
    final_review_report_path: Path | None
    rebuild_final_review: bool
    final_review_preserve_links: bool
    final_review_preserve_quotes: bool
    final_review_max_changed_chars_ratio: float
    final_review_allow_cron_apply: bool
    final_review_apply_policy: str
    final_review_max_patches_unattended: int
    final_review_max_changed_chars_unattended: int
    group_summaries_enabled: bool
    max_group_summary_calls: int
    group_summary_variant_policy: str
    min_usable_member_summaries: int
    dry_run: bool
    quiet: bool
    verbose: bool
    cron: bool
    mode: Literal["manual", "cron"]
    write_manifest: bool
    publish_latest: bool
    allow_partial_latest: bool
    grouping_enabled: bool
    grouping_min_group_size: int
    grouping_report: bool
    allow_summary_network: bool
    allow_final_review_network: bool
    allow_group_summary_network: bool
    apply_policy: ApplyPolicy | None

    @property
    def db_path(self) -> Path:
        return self.state_dir / "rollup.db"


def resolve_effective_run(
    config: Config,
    run_options: RunOptions,
    grouping: GroupingConfig | None = None,
) -> EffectiveRun:
    """Validate and flatten domain config plus invocation options for one run."""
    grouping = grouping or GroupingConfig()
    apply_policy = _validate_and_resolve_apply_policy(
        config, run_options=run_options, grouping=grouping
    )

    allow_summary_network = not run_options.dry_run and not config.no_ollama
    allow_final_review_network = (
        not run_options.dry_run and config.final_review_enabled
    )
    allow_group_summary_network = (
        not run_options.dry_run
        and config.group_summaries_enabled
        and not config.no_ollama
        and grouping.enabled
    )

    return EffectiveRun(
        root=config.root,
        mail_root=config.mail_root,
        output_dir=config.output_dir,
        state_dir=config.state_dir,
        log_dir=config.log_dir,
        lookback_days=config.lookback_days,
        folders_include=config.folders_include,
        folders_exclude=config.folders_exclude,
        no_ollama=config.no_ollama,
        include_seen_undated=config.include_seen_undated,
        rebuild_summaries=config.rebuild_summaries,
        max_body_chars=config.max_body_chars,
        max_chars_for_llm=config.max_chars_for_llm,
        max_display_links=config.max_display_links,
        ollama_url=config.ollama_url,
        ollama_model=config.ollama_model,
        allow_remote_ollama=config.allow_remote_ollama,
        summary_profile=config.summary_profile,
        summary_variants=config.summary_variants,
        summary_type_routing=config.summary_type_routing,
        summary_profile_set_path=config.summary_profile_set_path,
        export_summary_profile_set_path=config.export_summary_profile_set_path,
        list_summary_profiles=config.list_summary_profiles,
        list_newsletter_types=config.list_newsletter_types,
        summary_routing_report=config.summary_routing_report,
        final_review_enabled=config.final_review_enabled,
        final_review_mode=config.final_review_mode,
        final_review_profile=config.final_review_profile,
        final_review_provider=config.final_review_provider,
        final_review_model=config.final_review_model,
        final_review_report_path=config.final_review_report_path,
        rebuild_final_review=config.rebuild_final_review,
        final_review_preserve_links=config.final_review_preserve_links,
        final_review_preserve_quotes=config.final_review_preserve_quotes,
        final_review_max_changed_chars_ratio=config.final_review_max_changed_chars_ratio,
        final_review_allow_cron_apply=config.final_review_allow_cron_apply,
        final_review_apply_policy=config.final_review_apply_policy,
        final_review_max_patches_unattended=(
            config.final_review_max_patches_unattended
        ),
        final_review_max_changed_chars_unattended=(
            config.final_review_max_changed_chars_unattended
        ),
        group_summaries_enabled=config.group_summaries_enabled,
        max_group_summary_calls=config.max_group_summary_calls,
        group_summary_variant_policy=config.group_summary_variant_policy,
        min_usable_member_summaries=config.min_usable_member_summaries,
        dry_run=run_options.dry_run,
        quiet=run_options.quiet,
        verbose=run_options.verbose,
        cron=run_options.cron,
        mode=run_options.mode,
        write_manifest=run_options.write_manifest,
        publish_latest=run_options.publish_latest,
        allow_partial_latest=run_options.allow_partial_latest,
        grouping_enabled=grouping.enabled,
        grouping_min_group_size=grouping.min_group_size,
        grouping_report=grouping.report,
        allow_summary_network=allow_summary_network,
        allow_final_review_network=allow_final_review_network,
        allow_group_summary_network=allow_group_summary_network,
        apply_policy=apply_policy,
    )


def _validate_and_resolve_apply_policy(
    config: Config,
    *,
    run_options: RunOptions,
    grouping: GroupingConfig,
) -> ApplyPolicy | None:
    """Shared Phase-3 validation, kept here as the effective-run gate."""
    cron = bool(run_options.cron)
    grouping_enabled = bool(grouping.enabled)

    if getattr(config, "group_summary_profile", None) is not None:
        raise FinalReviewConfigError(
            "group_summary_profile has been removed; unset this key"
        )
    variant = getattr(config, "group_summary_variant_policy", "primary") or "primary"
    if variant != "primary":
        raise FinalReviewConfigError(
            f"Unsupported group_summary_variant_policy {variant!r}; "
            "only 'primary' is accepted"
        )

    if config.group_summaries_enabled:
        if config.no_ollama:
            raise FinalReviewConfigError(
                "--group-summaries requires Ollama (--ollama / no_ollama=False)"
            )
        if not grouping_enabled:
            raise FinalReviewConfigError(
                "--group-summaries requires grouping (not --no-grouping)"
            )

    if not config.final_review_enabled:
        if config.final_review_mode == "apply":
            raise FinalReviewConfigError(
                "--final-review-mode apply requires --final-review"
            )
        if config.final_review_allow_cron_apply:
            raise FinalReviewConfigError(
                "--final-review-allow-cron-apply requires --final-review "
                "and --final-review-mode apply"
            )
        return None

    validate_final_review_config(
        mode=config.final_review_mode,
        provider=config.final_review_provider,
        profile_name=config.final_review_profile,
    )
    validate_phase3_final_review_config(
        max_changed_chars_ratio=config.final_review_max_changed_chars_ratio
    )

    if config.final_review_allow_cron_apply and config.final_review_mode != "apply":
        raise FinalReviewConfigError(
            "--final-review-allow-cron-apply requires --final-review-mode apply"
        )

    if cron and config.final_review_mode == "apply":
        if not config.final_review_allow_cron_apply:
            raise FinalReviewConfigError(
                "Unattended apply requires --final-review-allow-cron-apply "
                "(fail closed)"
            )
        if config.final_review_apply_policy != "conservative":
            raise FinalReviewConfigError(
                "Cron apply only supports --final-review-apply-policy conservative"
            )

    if config.final_review_mode != "apply":
        return None

    return resolve_apply_policy(
        cron=cron,
        apply_policy_name=config.final_review_apply_policy,
        allow_cron_apply=config.final_review_allow_cron_apply,
        max_patches_unattended=config.final_review_max_patches_unattended,
        max_changed_chars_unattended=config.final_review_max_changed_chars_unattended,
        max_changed_chars_ratio=config.final_review_max_changed_chars_ratio,
        preserve_links=config.final_review_preserve_links,
        preserve_quotes=config.final_review_preserve_quotes,
        digest_budget_max_delta_chars=DIGEST_BUDGET_MAX_DELTA_CHARS,
    )
