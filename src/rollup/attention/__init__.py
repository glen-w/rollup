"""Attention / skip-safely ranking. Changes chrome, never inclusion."""

from rollup.attention.annotate import (
    annotate_items,
    apply_band_overrides,
    format_attention_report,
    high_relevance_entries,
    metadata_from_items,
    sort_items_by_attention,
)
from rollup.attention.config import InterestConfig, parse_interest_config
from rollup.attention.score import AttentionSignals, score_entry
from rollup.attention.signals import load_attention_signals

__all__ = [
    "AttentionSignals",
    "InterestConfig",
    "annotate_items",
    "apply_band_overrides",
    "format_attention_report",
    "high_relevance_entries",
    "load_attention_signals",
    "metadata_from_items",
    "parse_interest_config",
    "score_entry",
    "sort_items_by_attention",
]
