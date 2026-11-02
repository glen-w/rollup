"""Pipeline glue for attention annotation."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from rollup.attention.annotate import (
    annotate_items,
    apply_band_overrides,
    metadata_from_items,
    sort_items_by_attention,
)
from rollup.attention.config import InterestConfig
from rollup.attention.llm import complete_band_overrides
from rollup.attention.signals import load_attention_signals
from rollup.models import AttentionMetadata, DigestItem

logger = logging.getLogger(__name__)


def apply_attention(
    dated_items: tuple[DigestItem, ...],
    undated_items: tuple[DigestItem, ...],
    *,
    interest: InterestConfig,
    conn: sqlite3.Connection | None,
    snapshot=None,
    generated_at: datetime | None = None,
    allow_llm: bool = False,
    complete=None,
) -> tuple[tuple[DigestItem, ...], tuple[DigestItem, ...], AttentionMetadata]:
    signals = load_attention_signals(conn, snapshot, now=generated_at)
    profile = interest.profile if interest.present else ""
    dated = annotate_items(dated_items, profile_text=profile, signals=signals)
    undated = annotate_items(undated_items, profile_text=profile, signals=signals)
    llm_refined = False
    degraded = False
    if allow_llm and interest.present and complete is not None:
        combined = dated + undated
        try:
            overrides = complete_band_overrides(
                combined, profile_text=profile, complete=complete
            )
        except Exception as exc:
            logger.warning("Attention LLM refine failed: %s", exc)
            overrides = {}
            degraded = True
        if overrides:
            dated = apply_band_overrides(dated, overrides)
            undated = apply_band_overrides(undated, overrides)
            llm_refined = True
    dated = sort_items_by_attention(dated)
    undated = sort_items_by_attention(undated)
    meta = metadata_from_items(
        dated + undated,
        profile_present=interest.present,
        llm_refined=llm_refined,
        degraded=degraded,
    )
    return dated, undated, meta
