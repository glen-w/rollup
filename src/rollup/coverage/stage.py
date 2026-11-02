"""Pipeline glue for coverage clustering."""

from __future__ import annotations

import json
import logging

from rollup.briefing import iter_digest_entries
from rollup.coverage.cluster import (
    apply_clusters_to_items,
    apply_theme_overrides,
    cluster_entries,
)
from rollup.models import CoverageCluster, DigestItem

logger = logging.getLogger(__name__)


def apply_coverage(
    dated_items: tuple[DigestItem, ...],
    undated_items: tuple[DigestItem, ...],
    *,
    allow_llm: bool = False,
    complete=None,
) -> tuple[
    tuple[DigestItem, ...],
    tuple[DigestItem, ...],
    tuple[CoverageCluster, ...],
]:
    entries = iter_digest_entries(dated_items) + iter_digest_entries(undated_items)
    clusters = cluster_entries(entries)
    if allow_llm and complete is not None and clusters:
        try:
            labels = _theme_overrides(clusters, complete)
            clusters = apply_theme_overrides(clusters, labels)
        except Exception as exc:
            logger.warning("Coverage LLM theme naming failed: %s", exc)
    dated = apply_clusters_to_items(dated_items, clusters)
    undated = apply_clusters_to_items(undated_items, clusters)
    return dated, undated, clusters


def _theme_overrides(clusters: tuple[CoverageCluster, ...], complete) -> dict[str, str]:
    payload = [
        {
            "cluster_id": c.cluster_id,
            "theme_label": c.theme_label,
            "member_keys": list(c.member_keys),
        }
        for c in clusters
    ]
    prompt = (
        "Name each coverage cluster in a few words. Do not drop clusters.\n"
        'Return JSON {"items":[{"cluster_id":"...","theme_label":"..."}]}\n\n'
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    raw = complete(prompt)
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if not isinstance(data, list):
        return {}
    allowed = {c.cluster_id for c in clusters}
    out: dict[str, str] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        cid = row.get("cluster_id")
        label = row.get("theme_label")
        if cid in allowed and isinstance(label, str) and label.strip():
            out[cid] = label.strip()[:80]
    return out
