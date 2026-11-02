"""Fixed attention bands. Cutoffs are documented constants, not user knobs."""

from __future__ import annotations

from rollup.models import AttentionBand

# Inclusive lower bounds on the 0–1 heuristic score.
HIGH_RELEVANCE_MIN = 0.65
WORTH_SCANNING_MIN = 0.40
BACKGROUND_MIN = 0.20
NEUTRAL_SCORE = 0.50

BAND_RANK: dict[AttentionBand, int] = {
    "high_relevance": 0,
    "worth_scanning": 1,
    "background": 2,
    "safely_skip": 3,
}

BAND_LABELS: dict[AttentionBand, str] = {
    "high_relevance": "Start here",
    "worth_scanning": "Worth scanning",
    "background": "Background",
    "safely_skip": "Safely skip",
}


def band_for_score(score: float) -> AttentionBand:
    if score >= HIGH_RELEVANCE_MIN:
        return "high_relevance"
    if score >= WORTH_SCANNING_MIN:
        return "worth_scanning"
    if score >= BACKGROUND_MIN:
        return "background"
    return "safely_skip"


def band_rank(band: AttentionBand | None) -> int:
    if band is None:
        return BAND_RANK["worth_scanning"]
    return BAND_RANK.get(band, BAND_RANK["worth_scanning"])
