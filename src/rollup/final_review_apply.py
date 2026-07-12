"""Pure transformation: apply final-review patches to a DigestReport.

No in-place mutation; all changes are made via dataclasses.replace.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from dataclasses import replace

from rollup.config import Config
from rollup.models import (
    DigestEntry,
    DigestGroup,
    DigestItem,
    DigestReport,
    FinalReviewResult,
    PatchApplicationResult,
)

logger = logging.getLogger(__name__)

DIGEST_BUDGET_MAX_DELTA_CHARS: int = 2000
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_QUOTED_SPAN_RE = re.compile(r'"[^"]{2,}"')
_PROMPT_ARTEFACT_RE = re.compile(
    r"(Revised summary|```|overall_status)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Global-skip guard
# ---------------------------------------------------------------------------


def should_globally_skip_apply(result: FinalReviewResult) -> str | None:
    """Return a human-readable reason to skip all patches, or None to proceed."""
    if result.review_source == "error":
        return "review_source is 'error'"
    if result.overall_status == "fail":
        return f"overall_status is 'fail'"
    if not result.safe_to_publish:
        return "safe_to_publish is False"
    if not result.patches:
        return "no patches in result"
    if (
        result.echoed_digest_fingerprint
        and result.echoed_digest_fingerprint != result.digest_fingerprint
    ):
        return "echoed digest fingerprint mismatch"
    return None


# ---------------------------------------------------------------------------
# Entry-index helpers
# ---------------------------------------------------------------------------


def _build_entry_index(report: DigestReport) -> dict[str, DigestEntry]:
    """Return mapping of entry_id -> DigestEntry across all items."""
    index: dict[str, DigestEntry] = {}

    def _collect_entry(entry: DigestEntry) -> None:
        entry_id = entry.classified.parsed.message_key
        index[entry_id] = entry

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


# ---------------------------------------------------------------------------
# Text validation helpers
# ---------------------------------------------------------------------------


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
) -> str | None:
    """Return rejection reason string or None if acceptable."""
    if _nfkc_collapse(original) == _nfkc_collapse(replacement):
        return "replacement is identical after NFKC normalisation"

    if _PROMPT_ARTEFACT_RE.search(replacement):
        return "replacement contains prompt artefacts"

    orig_len = max(len(original), 40)
    repl_len = len(replacement)
    delta = repl_len - orig_len

    if delta > max_abs_ceiling:
        return (
            f"replacement too long: +{delta} chars exceeds ceiling of {max_abs_ceiling}"
        )

    orig_chars = max(len(original), 40)
    if orig_chars > 0 and abs(delta) / orig_chars > max_ratio:
        return (
            f"change ratio {abs(delta) / orig_chars:.3f} exceeds limit {max_ratio:.3f}"
        )

    if preserve_links:
        orig_urls = _extract_urls(original)
        repl_urls = _extract_urls(replacement)
        new_urls = repl_urls - orig_urls
        if new_urls:
            return f"replacement introduces new URLs: {list(new_urls)[:3]}"
        removed_urls = orig_urls - repl_urls
        if removed_urls:
            return f"replacement removes URLs: {list(removed_urls)[:3]}"

    if preserve_quotes:
        orig_quotes = _extract_quoted_spans(original)
        repl_quotes = _extract_quoted_spans(replacement)
        removed_quotes = orig_quotes - repl_quotes
        if removed_quotes:
            return f"replacement removes quoted spans: {list(removed_quotes)[:2]}"

    return None


# ---------------------------------------------------------------------------
# Report rebuilding helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Main apply function
# ---------------------------------------------------------------------------


def apply_final_review_patches(
    report: DigestReport,
    result: FinalReviewResult,
    config: Config,
) -> tuple[DigestReport, PatchApplicationResult]:
    """Apply patches from *result* to *report* without mutating either.

    Returns the (possibly updated) report and a PatchApplicationResult summary.
    All rejected/skipped patches are logged at DEBUG level.
    """
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
        )

    skip_reason = should_globally_skip_apply(result)
    if skip_reason:
        logger.debug("apply_final_review_patches: global skip – %s", skip_reason)
        counters["global_skip"] = len(result.patches)
        reasons.append(f"global_skip: {skip_reason}")
        return report, _make_result()

    entry_index = _build_entry_index(report)
    max_ratio = config.final_review_max_changed_chars_ratio
    preserve_links = config.final_review_preserve_links
    preserve_quotes = config.final_review_preserve_quotes

    applied_keys: set[str] = set()
    seen_issue_ids: set[str] = set()
    digest_delta_budget = DIGEST_BUDGET_MAX_DELTA_CHARS
    current_report = report

    issue_ids = {
        i.issue_id for i in result.issues if i.issue_id
    }

    for patch in result.patches:
        counters["attempted"] += 1

        if patch.issue_id:
            if patch.issue_id in seen_issue_ids:
                counters["duplicate"] += 1
                counters["rejected"] += 1
                reasons.append(f"duplicate_issue_id: {patch.issue_id!r}")
                continue
            seen_issue_ids.add(patch.issue_id)
            if issue_ids and patch.issue_id not in issue_ids:
                counters["unsafe"] += 1
                counters["rejected"] += 1
                reasons.append(f"unknown_issue_id: {patch.issue_id!r}")
                continue

        if patch.field != "summary":
            counters["invalid_field"] += 1
            counters["rejected"] += 1
            reasons.append(f"invalid_field: patch entry_id={patch.entry_id!r} field={patch.field!r}")
            logger.debug(
                "apply patch skip – invalid field %r for entry %r",
                patch.field,
                patch.entry_id,
            )
            continue

        entry_id = patch.entry_id
        entry = entry_index.get(entry_id)
        if entry is None:
            counters["unknown_entry"] += 1
            counters["rejected"] += 1
            reasons.append(f"unknown_entry: {entry_id!r}")
            logger.debug("apply patch skip – unknown entry %r", entry_id)
            continue

        if entry_id in applied_keys:
            counters["duplicate"] += 1
            counters["rejected"] += 1
            reasons.append(f"duplicate: {entry_id!r}")
            logger.debug("apply patch skip – duplicate entry %r", entry_id)
            continue

        original = entry.summary or ""
        replacement = patch.replacement.strip()

        orig_len = max(len(original), 40)
        abs_ceiling = max(200, int(0.5 * orig_len))

        rejection = _validate_replacement(
            original,
            replacement,
            preserve_links=preserve_links,
            preserve_quotes=preserve_quotes,
            max_ratio=max_ratio,
            max_abs_ceiling=abs_ceiling,
        )
        if rejection is not None:
            if "ratio" in rejection or "long" in rejection:
                counters["ratio_exceeded"] += 1
            elif "URL" in rejection or "quoted" in rejection:
                counters["preservation_failed"] += 1
            else:
                counters["invalid_content"] += 1
            counters["rejected"] += 1
            reasons.append(f"rejected ({entry_id!r}): {rejection}")
            logger.debug("apply patch reject entry %r – %s", entry_id, rejection)
            continue

        delta = abs(len(replacement) - len(original))
        if delta > digest_delta_budget:
            counters["ratio_exceeded"] += 1
            counters["rejected"] += 1
            reasons.append(
                f"digest_budget_exceeded ({entry_id!r}): delta={delta} budget={digest_delta_budget}"
            )
            logger.debug(
                "apply patch skip – digest budget exhausted for entry %r", entry_id
            )
            continue

        new_entry = replace(
            entry,
            summary=replacement,
            summary_source="final_review_applied",
            summary_original=original if original else None,
        )
        current_report = _apply_entry_replacement(current_report, entry_id, new_entry)
        entry_index[entry_id] = new_entry
        applied_keys.add(entry_id)
        digest_delta_budget -= delta
        counters["applied"] += 1
        logger.debug("apply patch applied entry %r (+%d chars delta)", entry_id, delta)

    return current_report, _make_result()
