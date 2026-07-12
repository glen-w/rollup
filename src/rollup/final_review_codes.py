"""Stable reason codes and resolved apply policy for final-review hardening.

Machine telemetry (manifests, counters) must use these codes only.
Human-readable detail stays in debug logs / PatchApplicationResult.reasons.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ApplySkipReason = Literal[
    "review_source_error",
    "overall_status_fail",
    "unsafe_to_publish",
    "no_patches",
    "fingerprint_missing",
    "fingerprint_mismatch",
    "issue_ids_not_unique",
    "unattended_patch_cap",
    "unattended_char_cap",
    "cron_apply_disallowed",
]

PatchRejectReason = Literal[
    "missing_issue_id",
    "unknown_issue_id",
    "safe_auto_fix_not_true",
    "duplicate_issue_id",
    "duplicate_entry",
    "conflicting_replacement",
    "unknown_entry",
    "invalid_field",
    "identical_nfkc",
    "prompt_artefact",
    "ratio_exceeded",
    "abs_ceiling_exceeded",
    "digest_budget_exceeded",
    "url_preservation",
    "quote_preservation",
    "oversized_id",
]

GroupSummaryErrorCode = Literal[
    "cache_schema_absent",
    "cache_read_corrupt",
    "cache_read_error",
    "cache_write_error",
    "stream_truncated",
    "stream_timeout",
    "stream_malformed",
    "response_oversized",
    "ollama_http_error",
    "retry_exhausted",
    "ineligible",
    "budget_skipped",
]

APPLY_SKIP_REASONS: frozenset[str] = frozenset(
    {
        "review_source_error",
        "overall_status_fail",
        "unsafe_to_publish",
        "no_patches",
        "fingerprint_missing",
        "fingerprint_mismatch",
        "issue_ids_not_unique",
        "unattended_patch_cap",
        "unattended_char_cap",
        "cron_apply_disallowed",
    }
)

PATCH_REJECT_REASONS: frozenset[str] = frozenset(
    {
        "missing_issue_id",
        "unknown_issue_id",
        "safe_auto_fix_not_true",
        "duplicate_issue_id",
        "duplicate_entry",
        "conflicting_replacement",
        "unknown_entry",
        "invalid_field",
        "identical_nfkc",
        "prompt_artefact",
        "ratio_exceeded",
        "abs_ceiling_exceeded",
        "digest_budget_exceeded",
        "url_preservation",
        "quote_preservation",
        "oversized_id",
    }
)

GROUP_SUMMARY_ERROR_CODES: frozenset[str] = frozenset(
    {
        "cache_schema_absent",
        "cache_read_corrupt",
        "cache_read_error",
        "cache_write_error",
        "stream_truncated",
        "stream_timeout",
        "stream_malformed",
        "response_oversized",
        "ollama_http_error",
        "retry_exhausted",
        "ineligible",
        "budget_skipped",
    }
)

# Hard cap on issue_id / entry_id string length in patches and issues.
MAX_ID_CHARS = 256

DIGEST_BUDGET_MAX_DELTA_CHARS: int = 2000


@dataclass(frozen=True)
class ApplyPolicy:
    """Resolved once before apply; apply logic must not re-infer --cron."""

    unattended: bool
    allow_cron_apply: bool
    cron_mode: bool
    max_patches_unattended: int
    max_changed_chars_unattended: int
    max_changed_chars_ratio: float
    preserve_links: bool
    preserve_quotes: bool
    digest_budget_max_delta_chars: int = DIGEST_BUDGET_MAX_DELTA_CHARS


def resolve_apply_policy(
    *,
    cron: bool,
    apply_policy_name: str,
    allow_cron_apply: bool,
    max_patches_unattended: int,
    max_changed_chars_unattended: int,
    max_changed_chars_ratio: float,
    preserve_links: bool,
    preserve_quotes: bool,
    digest_budget_max_delta_chars: int = DIGEST_BUDGET_MAX_DELTA_CHARS,
) -> ApplyPolicy:
    unattended = bool(cron) or apply_policy_name == "conservative"
    return ApplyPolicy(
        unattended=unattended,
        allow_cron_apply=allow_cron_apply,
        cron_mode=bool(cron),
        max_patches_unattended=max_patches_unattended,
        max_changed_chars_unattended=max_changed_chars_unattended,
        max_changed_chars_ratio=max_changed_chars_ratio,
        preserve_links=preserve_links,
        preserve_quotes=preserve_quotes,
        digest_budget_max_delta_chars=digest_budget_max_delta_chars,
    )
