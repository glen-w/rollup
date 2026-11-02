"""Optional LLM reband. May only change bands, never drop keys."""

from __future__ import annotations

import json
import logging
from typing import Any

from rollup.attention.bands import BAND_RANK
from rollup.briefing import iter_digest_entries
from rollup.models import AttentionBand, DigestItem

logger = logging.getLogger(__name__)

_VALID_BANDS = frozenset(BAND_RANK)


def parse_band_overrides(raw: str) -> dict[str, AttentionBand]:
    """Parse a JSON array of ``{message_key, band}``. Invalid rows are skipped."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict) and "items" in payload:
        payload = payload["items"]
    if not isinstance(payload, list):
        return {}
    out: dict[str, AttentionBand] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        key = row.get("message_key")
        band = row.get("band")
        if isinstance(key, str) and key and band in _VALID_BANDS:
            out[key] = band
    return out


def build_reband_prompt(
    items: tuple[DigestItem, ...],
    profile_text: str,
) -> str:
    rows = []
    for entry in iter_digest_entries(items):
        p = entry.classified.parsed
        rows.append(
            {
                "message_key": p.message_key,
                "subject": p.subject,
                "summary": (entry.summary or p.preview or "")[:400],
                "heuristic_band": entry.attention_band,
            }
        )
    return (
        "Assign each item an attention band for a personal briefing.\n"
        "Bands: high_relevance, worth_scanning, background, safely_skip.\n"
        "You may only reband; never omit a message_key from the input.\n"
        "Return JSON: {\"items\": [{\"message_key\": \"...\", \"band\": \"...\"}]}\n\n"
        f"Interest profile:\n{profile_text.strip() or '(none)'}\n\n"
        f"Items:\n{json.dumps(rows, ensure_ascii=False)}"
    )


def complete_band_overrides(
    items: tuple[DigestItem, ...],
    *,
    profile_text: str,
    complete: Any,
) -> dict[str, AttentionBand]:
    """Call *complete(prompt) -> str*. Failures return empty overrides."""
    prompt = build_reband_prompt(items, profile_text)
    try:
        raw = complete(prompt)
    except Exception as exc:
        logger.warning("Attention LLM refine failed: %s", exc)
        return {}
    if not isinstance(raw, str) or not raw.strip():
        return {}
    allowed = {
        entry.classified.parsed.message_key for entry in iter_digest_entries(items)
    }
    parsed = parse_band_overrides(raw)
    return {key: band for key, band in parsed.items() if key in allowed}
