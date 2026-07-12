"""Pure transformation: apply final-review patches to a DigestReport.

No in-place mutation; all changes are made via dataclasses.replace.

Fingerprint provenance (host truth):
  compute_digest_fingerprint hashes a canonical JSON list of entry fingerprints
  (sorted folder keys; report order within folders; undated labeled \"undated\").
  Computed once at the start of execute_final_review. Echo must come from the
  model/cache payload only — never synthesised.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from dataclasses import replace

from rollup.config import Config
from rollup.final_review_codes import (
    DIGEST_BUDGET_MAX_DELTA_CHARS,
    MAX_ID_CHARS,
    ApplyPolicy,
    ApplySkipReason,
    PatchRejectReason,
    resolve_apply_policy,
)
from rollup.models import (
    DigestEntry,
    DigestGroup,
    DigestItem,
    DigestReport,
    FinalReviewPatch,
    FinalReviewResult,
    PatchApplicationResult,
)

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_QUOTED_SPAN_RE = re.compile(r'"[^"]{2,}"')
_PROMPT_ARTEFACT_RE = re.compile(
    r"(Revised summary|```|overall_status)",
    re.IGNORECASE,
)


def should_globally_skip_apply(
    result: FinalReviewResult,
    *,
    policy: ApplyPolicy | None = None,
    report: DigestReport | None = None,
) -> ApplySkipReason | None:
    """Return a stable skip code, or None to proceed past publication gates."""
    if result.review_source == "error":
        return "review_source_error"
    if result.overall_status == "fail":
        return "overall_status_fail"
    if result.safe_to_publish is not True:
        return "unsafe_to_publish"
    if not result.patches:
        return "no_patches"
    echo = result.echoed_digest_fingerprint
    if echo is None or (isinstance(echo, str) and not echo.strip()):
        return "fingerprint_missing"
    if echo != result.digest_fingerprint:
        return "fingerprint_mismatch"
    if report is not None:
        from rollup.final_review import compute_digest_fingerprint

        if compute_digest_fingerprint(report) != result.digest_fingerprint:
            return "fingerprint_mismatch"
    # Issue-id uniqueness among non-empty ids
    seen: set[str] = set()
    for issue in result.issues:
        if not issue.issue_id:
            continue
        if issue.issue_id in seen:
            return "issue_ids_not_unique"
        seen.add(issue.issue_id)
    if policy is not None and policy.cron_mode and not policy.allow_cron_apply:
        return "cron_apply_disallowed"
    return None


def _build_entry_index(report: DigestReport) -> dict[str, DigestEntry]:
    index: dict[str, DigestEntry] = {}

    def _collect_entry(entry: DigestEntry) -> None:
        index[entry.classified.parsed.message_key] = entry

    def _walk_items(items: tuple[DigestItem, ...]) -> None:
        for item in items:
            if isinstance(item, DigestEntry):
                _collect_entry(item)
            elif isinstance(item, DigestGroup):
                for e in item.entries:
                    _collect_entry(e)

    for folder_items in report.dated_by_folder.values():
        _walk_items(folder_items)
    _walk_items(report.undated)
    return index


def _nfkc_collapse(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _extract_urls(text: str) -> Counter[str]:
    return Counter(_URL_RE.findall(text))


def _extract_quoted_spans(text: str) -> Counter[str]:
    return Counter(_QUOTED_SPAN_RE.findall(text))


def _validate_replacement(
    original: str,
    replacement: str,
    *,
    preserve_links: bool,
    preserve_quotes: bool,
    max_ratio: float,
    max_abs_ceiling: int,
) -> PatchRejectReason | None:
    if _nfkc_collapse(original) == _nfkc_collapse(replacement):
        return "identical_nfkc"
    if _PROMPT_ARTEFACT_RE.search(replacement):
        return "prompt_artefact"

    orig_len = max(len(original), 40)
    repl_len = len(replacement)
    delta = repl_len - orig_len

    if delta > max_abs_ceiling:
        return "abs_ceiling_exceeded"
    if orig_len > 0 and abs(delta) / orig_len > max_ratio:
        return "ratio_exceeded"

    if preserve_links:
        orig_urls = _extract_urls(original)
        repl_urls = _extract_urls(replacement)
        if repl_urls - orig_urls or orig_urls - repl_urls:
            return "url_preservation"

    if preserve_quotes:
        orig_quotes = _extract_quoted_spans(original)
        repl_quotes = _extract_quoted_spans(replacement)
        if orig_quotes - repl_quotes:
            return "quote_preservation"

    return None


def _replace_entry_in_items(
    items: tuple[DigestItem, ...],
    target_key: str,
    new_entry: DigestEntry,
) -> tuple[DigestItem, ...]:
    result: list[DigestItem] = []
    for item in items:
        if isinstance(item, DigestEntry):
            if item.classified.parsed.message_key == target_key:
                result.append(new_entry)
            else:
                result.append(item)
        elif isinstance(item, DigestGroup):
            new_group_entries: list[DigestEntry] = []
            changed = False
            for e in item.entries:
                if e.classified.parsed.message_key == target_key:
                    new_group_entries.append(new_entry)
                    changed = True
                else:
                    new_group_entries.append(e)
            if changed:
                result.append(replace(item, entries=tuple(new_group_entries)))
            else:
                result.append(item)
        else:
            result.append(item)
    return tuple(result)


def _apply_entry_replacement(
    report: DigestReport,
    target_key: str,
    new_entry: DigestEntry,
) -> DigestReport:
    new_dated: dict[str, tuple[DigestItem, ...]] = {}
    for folder, items in report.dated_by_folder.items():
        new_dated[folder] = _replace_entry_in_items(items, target_key, new_entry)
    new_undated = _replace_entry_in_items(report.undated, target_key, new_entry)
    return replace(report, dated_by_folder=new_dated, undated=new_undated)


def _bucket_legacy_counter(code: PatchRejectReason, counters: dict[str, int]) -> None:
    if code in ("duplicate_issue_id", "duplicate_entry", "conflicting_replacement"):
        counters["duplicate"] += 1
    elif code in (
        "missing_issue_id",
        "unknown_issue_id",
        "safe_auto_fix_not_true",
        "oversized_id",
    ):
        counters["unsafe"] += 1
    elif code == "unknown_entry":
        counters["unknown_entry"] += 1
    elif code == "invalid_field":
        counters["invalid_field"] += 1
    elif code in ("ratio_exceeded", "abs_ceiling_exceeded", "digest_budget_exceeded"):
        counters["ratio_exceeded"] += 1
    elif code in ("url_preservation", "quote_preservation"):
        counters["preservation_failed"] += 1
    else:
        counters["invalid_content"] += 1


def _policy_from_config(config: Config, policy: ApplyPolicy | None) -> ApplyPolicy:
    if policy is not None:
        return policy
    return resolve_apply_policy(
        cron=False,
        apply_policy_name=getattr(config, "final_review_apply_policy", "conservative"),
        allow_cron_apply=getattr(config, "final_review_allow_cron_apply", False),
        max_patches_unattended=getattr(config, "final_review_max_patches_unattended", 5),
        max_changed_chars_unattended=getattr(
            config, "final_review_max_changed_chars_unattended", 800
        ),
        max_changed_chars_ratio=config.final_review_max_changed_chars_ratio,
        preserve_links=config.final_review_preserve_links,
        preserve_quotes=config.final_review_preserve_quotes,
        digest_budget_max_delta_chars=DIGEST_BUDGET_MAX_DELTA_CHARS,
    )


def apply_final_review_patches(
    report: DigestReport,
    result: FinalReviewResult,
    config: Config,
    *,
    policy: ApplyPolicy | None = None,
) -> tuple[DigestReport, PatchApplicationResult]:
    """Apply patches from *result* to *report* without mutating either.

    Budget ordering:
      1) Global-skip gates
      2) Validate every patch individually; collect would-succeed set
      3) Unattended caps → whole-set global skip if exceeded
      4) Interactive digest budget: per-patch reject, continue
    """
    resolved = _policy_from_config(config, policy)
    counters: dict[str, int] = {
        "attempted": 0,
        "applied": 0,
        "rejected": 0,
        "duplicate": 0,
        "unknown_entry": 0,
        "unsafe": 0,
        "invalid_field": 0,
        "invalid_content": 0,
        "ratio_exceeded": 0,
        "preservation_failed": 0,
        "global_skip": 0,
    }
    reasons: list[str] = []
    reject_counts: Counter[str] = Counter()
    global_skip_reason: ApplySkipReason | None = None

    def _make_result() -> PatchApplicationResult:
        return PatchApplicationResult(
            attempted=counters["attempted"],
            applied=counters["applied"],
            rejected=counters["rejected"],
            duplicate=counters["duplicate"],
            unknown_entry=counters["unknown_entry"],
            unsafe=counters["unsafe"],
            invalid_field=counters["invalid_field"],
            invalid_content=counters["invalid_content"],
            ratio_exceeded=counters["ratio_exceeded"],
            preservation_failed=counters["preservation_failed"],
            global_skip=counters["global_skip"],
            reasons=tuple(reasons),
            global_skip_reason=global_skip_reason,
            reject_counts=tuple(sorted(reject_counts.items())),
        )

    def _global_skip(code: ApplySkipReason) -> tuple[DigestReport, PatchApplicationResult]:
        nonlocal global_skip_reason
        global_skip_reason = code
        logger.info("apply_final_review_patches: global skip – %s", code)
        counters["global_skip"] = len(result.patches)
        counters["attempted"] = len(result.patches)
        reasons.append(f"global_skip:{code}")
        return report, _make_result()

    skip = should_globally_skip_apply(result, policy=resolved, report=report)
    if skip is not None:
        return _global_skip(skip)

    entry_index = _build_entry_index(report)
    issues_by_id = {
        i.issue_id: i for i in result.issues if i.issue_id
    }

    # Pre-scan conflicts: entry_id -> first replacement
    first_replacement: dict[str, str] = {}
    conflicting_entries: set[str] = set()
    for patch in result.patches:
        prev = first_replacement.get(patch.entry_id)
        if prev is None:
            first_replacement[patch.entry_id] = patch.replacement
        elif prev != patch.replacement:
            conflicting_entries.add(patch.entry_id)

    # Pass 1: validate each patch; collect candidates
    candidates: list[tuple[FinalReviewPatch, DigestEntry, str, int]] = []
    seen_issue_ids: set[str] = set()
    seen_entry_ids: set[str] = set()

    for patch in result.patches:
        counters["attempted"] += 1
        code: PatchRejectReason | None = None

        if not patch.issue_id:
            code = "missing_issue_id"
        elif len(patch.issue_id) > MAX_ID_CHARS or len(patch.entry_id) > MAX_ID_CHARS:
            code = "oversized_id"
        elif patch.entry_id in conflicting_entries:
            code = "conflicting_replacement"
        elif patch.issue_id in seen_issue_ids:
            code = "duplicate_issue_id"
        elif patch.entry_id in seen_entry_ids:
            code = "duplicate_entry"
        elif patch.issue_id not in issues_by_id:
            code = "unknown_issue_id"
        elif issues_by_id[patch.issue_id].safe_auto_fix is not True:
            code = "safe_auto_fix_not_true"
        elif patch.field != "summary":
            # Source policy / display / priority / grouping are not patchable.
            code = "invalid_field"
        else:
            entry = entry_index.get(patch.entry_id)
            if entry is None:
                code = "unknown_entry"
            else:
                original = entry.summary or ""
                replacement = patch.replacement.strip()
                orig_len = max(len(original), 40)
                abs_ceiling = max(200, int(0.5 * orig_len))
                code = _validate_replacement(
                    original,
                    replacement,
                    preserve_links=resolved.preserve_links,
                    preserve_quotes=resolved.preserve_quotes,
                    max_ratio=resolved.max_changed_chars_ratio,
                    max_abs_ceiling=abs_ceiling,
                )
                if code is None:
                    delta = abs(len(replacement) - len(original))
                    seen_issue_ids.add(patch.issue_id)
                    seen_entry_ids.add(patch.entry_id)
                    candidates.append((patch, entry, replacement, delta))
                    continue

        assert code is not None
        reject_counts[code] += 1
        _bucket_legacy_counter(code, counters)
        counters["rejected"] += 1
        reasons.append(f"{code}:{patch.entry_id}")
        logger.debug("apply patch reject entry %r – %s", patch.entry_id, code)

    # Unattended whole-set caps (prefer global skip over partial apply)
    if resolved.unattended and candidates:
        total_delta = sum(d for *_, d in candidates)
        if len(candidates) > resolved.max_patches_unattended:
            return _global_skip("unattended_patch_cap")
        if total_delta > resolved.max_changed_chars_unattended:
            return _global_skip("unattended_char_cap")

    # Pass 2: apply candidates (interactive digest budget may reject individually)
    digest_budget = resolved.digest_budget_max_delta_chars
    current_report = report

    for patch, entry, replacement, delta in candidates:
        if not resolved.unattended and delta > digest_budget:
            reject_counts["digest_budget_exceeded"] += 1
            _bucket_legacy_counter("digest_budget_exceeded", counters)
            counters["rejected"] += 1
            reasons.append(f"digest_budget_exceeded:{patch.entry_id}")
            continue

        original = entry.summary or ""
        new_entry = replace(
            entry,
            summary=replacement,
            summary_source="final_review_applied",
            summary_original=original if original else None,
        )
        current_report = _apply_entry_replacement(
            current_report, patch.entry_id, new_entry
        )
        entry_index[patch.entry_id] = new_entry
        if not resolved.unattended:
            digest_budget -= delta
        counters["applied"] += 1
        logger.debug("apply patch applied entry %r (delta=%d)", patch.entry_id, delta)

    return current_report, _make_result()
