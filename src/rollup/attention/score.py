"""Deterministic attention score from interest prose, Quality, and interaction."""

from __future__ import annotations

from dataclasses import dataclass, field

from rollup.attention.bands import NEUTRAL_SCORE, WORTH_SCANNING_MIN, band_for_score
from rollup.attention.profile import jaccard, tokenize
from rollup.models import AttentionBand, DigestEntry


@dataclass(frozen=True)
class AttentionSignals:
    """Per-run lookup tables. Empty means those channels contribute nothing."""

    quality_by_source: dict[str, float] = field(default_factory=dict)
    priority_by_source: dict[str, int] = field(default_factory=dict)
    saved: frozenset[str] = frozenset()
    dismissed: frozenset[str] = frozenset()
    read: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AttentionScore:
    score: float
    band: AttentionBand
    reasons: tuple[str, ...]


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def score_entry(
    entry: DigestEntry,
    *,
    profile_text: str,
    signals: AttentionSignals,
    profile_tokens: frozenset[str] | None = None,
) -> AttentionScore:
    parsed = entry.classified.parsed
    key = parsed.message_key
    source = parsed.source_key or ""
    text = " ".join(
        part
        for part in (parsed.subject, parsed.preview, entry.summary or "")
        if part
    )
    tokens = tokenize(text)
    interest = profile_tokens if profile_tokens is not None else tokenize(profile_text)
    lex = jaccard(tokens, interest) if interest else 0.0

    adjusted = signals.quality_by_source.get(source)
    quality_n = ((adjusted - 1.0) / 4.0) if adjusted is not None else None
    priority = int(signals.priority_by_source.get(source, 0) or 0)
    prio_n = _clip(priority / 100.0)

    saved = key in signals.saved
    dismissed = key in signals.dismissed and not saved
    read = key in signals.read
    interact = 0.0
    if saved:
        interact += 0.35
    if dismissed:
        interact -= 0.35
    if read and not dismissed:
        interact += 0.05
    interact = _clip(0.5 + interact)

    no_profile = not interest
    no_quality = quality_n is None
    no_interact = not saved and not dismissed and not read
    no_priority = priority <= 0
    if no_profile and no_quality and no_interact and no_priority:
        return AttentionScore(
            score=NEUTRAL_SCORE,
            band="worth_scanning",
            reasons=(),
        )

    quality_term = quality_n if quality_n is not None else NEUTRAL_SCORE
    score = _clip(
        0.50 * lex
        + 0.20 * quality_term
        + 0.20 * interact
        + 0.10 * prio_n
    )
    # A profile with no lexical hit is still "worth scanning", not background,
    # unless dismissal or low quality pulled the score down.
    low_quality = quality_n is not None and quality_n <= 0.25
    if lex < 0.12 and not dismissed and not low_quality:
        score = max(score, WORTH_SCANNING_MIN)
    reasons: list[str] = []
    if lex >= 0.12:
        reasons.append("interest-match")
    if saved:
        reasons.append("saved")
    if dismissed:
        reasons.append("dismissed")
    if quality_n is not None and quality_n >= 0.75:
        reasons.append("high-quality")
    if quality_n is not None and quality_n <= 0.25:
        reasons.append("low-quality")
    if priority >= 50:
        reasons.append("source-priority")
    return AttentionScore(
        score=round(score, 4),
        band=band_for_score(score),
        reasons=tuple(reasons),
    )
