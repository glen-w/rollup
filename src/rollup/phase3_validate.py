"""Central Phase-3 / hardening runtime config validation.

Used by CLI argparse paths and any config-file / programmatic Config builds
so invalid combinations fail the same way everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

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
class ValidatedPhase3Runtime:
    """Result of central validation; carry resolved apply policy into the pipeline."""

    apply_policy: ApplyPolicy | None


def validate_phase3_runtime_config(
    config: Config,
    *,
    run_options: RunOptions | None = None,
    grouping: GroupingConfig | None = None,
) -> ValidatedPhase3Runtime:
    """Validate Phase-3 flags and resolve ApplyPolicy.

    Precedence: structural final-review → group-summary → unattended apply gates.
    Raises FinalReviewConfigError (or ValueError subclass) on hard errors.
    """
    cron = bool(run_options.cron) if run_options is not None else False
    grouping_enabled = True if grouping is None else bool(grouping.enabled)

    # --- Dead / unsupported knobs ---
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

    # --- Group summaries ---
    if config.group_summaries_enabled:
        if config.no_ollama:
            raise FinalReviewConfigError(
                "--group-summaries requires Ollama (--ollama / no_ollama=False)"
            )
        if not grouping_enabled:
            raise FinalReviewConfigError(
                "--group-summaries requires grouping (not --no-grouping)"
            )

    # --- Final review structural ---
    apply_policy: ApplyPolicy | None = None
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
        return ValidatedPhase3Runtime(apply_policy=None)

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

    # --- Unattended apply gates ---
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

    if config.final_review_mode == "apply":
        apply_policy = resolve_apply_policy(
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

    return ValidatedPhase3Runtime(apply_policy=apply_policy)
