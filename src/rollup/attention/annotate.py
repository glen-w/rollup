"""Annotate digest items with attention bands. Never drops included mail."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from rollup.attention.bands import BAND_LABELS, band_rank
from rollup.attention.profile import tokenize
from rollup.attention.score import AttentionScore, AttentionSignals, score_entry
from rollup.briefing import included_message_keys, iter_digest_entries
from rollup.models import (
    AttentionBand,
    AttentionMetadata,
    DigestEntry,
    DigestGroup,
    DigestItem,
    DigestReport,
)


def annotate_entry(
    entry: DigestEntry,
    *,
    profile_text: str,
    signals: AttentionSignals,
    profile_tokens: frozenset[str] | None = None,
) -> DigestEntry:
    scored = score_entry(
        entry,
        profile_text=profile_text,
        signals=signals,
        profile_tokens=profile_tokens,
    )
    return replace(
        entry,
        attention_band=scored.band,
        attention_score=scored.score,
        attention_reasons=scored.reasons,
    )


def annotate_items(
    items: tuple[DigestItem, ...],
    *,
    profile_text: str,
    signals: AttentionSignals,
) -> tuple[DigestItem, ...]:
    """Return items with annotated entries. Group membership is unchanged."""
    tokens = tokenize(profile_text)
    out: list[DigestItem] = []
    for item in items:
        if isinstance(item, DigestGroup):
            entries = tuple(
                annotate_entry(
                    entry,
                    profile_text=profile_text,
                    signals=signals,
                    profile_tokens=tokens,
                )
                for entry in item.entries
            )
            out.append(replace(item, entries=entries))
        else:
            out.append(
                annotate_entry(
                    item,
                    profile_text=profile_text,
                    signals=signals,
                    profile_tokens=tokens,
                )
            )
    annotated = tuple(out)
    _require_inclusion(items, annotated)
    return annotated


def apply_band_overrides(
    items: tuple[DigestItem, ...],
    overrides: dict[str, AttentionBand],
) -> tuple[DigestItem, ...]:
    """Reband existing annotated entries. Unknown keys are ignored. No drops."""

    def _one(entry: DigestEntry) -> DigestEntry:
        key = entry.classified.parsed.message_key
        band = overrides.get(key)
        if band is None or band == entry.attention_band:
            return entry
        reasons = entry.attention_reasons
        if "llm-reband" not in reasons:
            reasons = reasons + ("llm-reband",)
        return replace(entry, attention_band=band, attention_reasons=reasons)

    out: list[DigestItem] = []
    for item in items:
        if isinstance(item, DigestGroup):
            out.append(
                replace(item, entries=tuple(_one(entry) for entry in item.entries))
            )
        else:
            out.append(_one(item))
    annotated = tuple(out)
    _require_inclusion(items, annotated)
    return annotated


def item_attention_rank(item: DigestItem) -> int:
    if isinstance(item, DigestGroup):
        ranks = [band_rank(e.attention_band) for e in item.entries]
        return min(ranks) if ranks else band_rank(None)
    return band_rank(item.attention_band)


def sort_items_by_attention(items: tuple[DigestItem, ...]) -> tuple[DigestItem, ...]:
    """Band first (high → skip), then existing read-time order as a tie-break."""

    def key(item: DigestItem) -> tuple:
        rank = item_attention_rank(item)
        if isinstance(item, DigestGroup):
            minutes = sum(e.classified.parsed.read_time_minutes for e in item.entries)
            return (rank, 0, minutes, item.display_name.lower())
        return (
            rank,
            1,
            item.classified.parsed.read_time_minutes,
            item.classified.parsed.subject.lower(),
        )

    return tuple(sorted(items, key=key))


def high_relevance_entries(report: DigestReport) -> tuple[DigestEntry, ...]:
    dated = tuple(
        entry
        for items in report.dated_by_folder.values()
        for entry in iter_digest_entries(items)
        if entry.attention_band == "high_relevance"
    )
    undated = tuple(
        entry
        for entry in iter_digest_entries(report.undated)
        if entry.attention_band == "high_relevance"
    )
    return dated + undated


def metadata_from_items(
    items: tuple[DigestItem, ...],
    *,
    profile_present: bool,
    llm_refined: bool = False,
    degraded: bool = False,
) -> AttentionMetadata:
    counts: Counter[str] = Counter()
    for entry in iter_digest_entries(items):
        band = entry.attention_band or "worth_scanning"
        counts[band] += 1
    order = ("high_relevance", "worth_scanning", "background", "safely_skip")
    band_counts = tuple((band, counts[band]) for band in order if counts[band])
    return AttentionMetadata(
        profile_present=profile_present,
        llm_refined=llm_refined,
        band_counts=band_counts,
        degraded=degraded,
    )


def format_attention_report(metadata: AttentionMetadata) -> str:
    lines = ["Attention:"]
    if metadata.profile_present:
        lines.append("  profile: yes")
    else:
        lines.append("  profile: no")
    if metadata.llm_refined:
        lines.append("  llm_refined: yes")
    if metadata.degraded:
        lines.append("  degraded: yes")
    for band, count in metadata.band_counts:
        lines.append(f"  {BAND_LABELS.get(band, band)}: {count}")
    return "\n".join(lines)


def _require_inclusion(
    before: tuple[DigestItem, ...], after: tuple[DigestItem, ...]
) -> None:
    if included_message_keys(before) != included_message_keys(after):
        raise RuntimeError("attention annotation dropped or added message keys")
