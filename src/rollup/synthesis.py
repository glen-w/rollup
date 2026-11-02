"""Briefing-level 'what mattered' layer. Not final-review QA."""

from __future__ import annotations

import json
import logging
from dataclasses import replace

from rollup.attention.annotate import high_relevance_entries
from rollup.models import BriefingSynthesis, DigestReport

logger = logging.getLogger(__name__)
MAX_BULLETS = 8


def build_glance(report: DigestReport) -> BriefingSynthesis | None:
    """Deterministic offline glance. Empty window yields None."""
    bullets: list[str] = []
    for entry in high_relevance_entries(report)[:5]:
        bullets.append(entry.classified.parsed.subject.strip())
    for cluster in report.coverage_clusters[:3]:
        n = len(cluster.member_keys)
        bullets.append(f"Covered {n} ways: {cluster.theme_label}")
    folder_n = len(report.dated_by_folder)
    included = report.stats.dated_included + report.stats.undated_needing_review
    if included == 0:
        return None
    if folder_n:
        bullets.append(f"{folder_n} folder(s) · {included} items in the window")
    # Dedupe while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for line in bullets:
        if line and line not in seen:
            seen.add(line)
            unique.append(line)
        if len(unique) >= MAX_BULLETS:
            break
    if not unique:
        unique.append(f"{included} item(s) in the collection window")
    return BriefingSynthesis(
        headline="This window at a glance",
        bullets=tuple(unique),
        source="glance",
    )


def parse_narrative(raw: str) -> BriefingSynthesis | None:
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    headline = data.get("headline")
    if headline is not None and not isinstance(headline, str):
        headline = None
    bullets_raw = data.get("bullets") or []
    skip_raw = data.get("what_to_skip") or []
    if not isinstance(bullets_raw, list) or not isinstance(skip_raw, list):
        return None
    bullets = tuple(
        str(item).strip() for item in bullets_raw if str(item).strip()
    )[:MAX_BULLETS]
    skip = tuple(str(item).strip() for item in skip_raw if str(item).strip())[:MAX_BULLETS]
    if not bullets:
        return None
    return BriefingSynthesis(
        headline=(headline.strip() if headline and headline.strip() else None),
        bullets=bullets,
        what_to_skip=skip,
        source="ollama",
    )


def build_narrative_prompt(report: DigestReport, glance: BriefingSynthesis) -> str:
    titles = [
        entry.classified.parsed.subject
        for entry in high_relevance_entries(report)[:12]
    ]
    if not titles:
        from rollup.briefing import iter_digest_entries

        titles = [
            e.classified.parsed.subject
            for items in report.dated_by_folder.values()
            for e in iter_digest_entries(items)
        ][:12]
    clusters = [
        {"theme": c.theme_label, "n": len(c.member_keys), "sources": c.source_count}
        for c in report.coverage_clusters[:8]
    ]
    return (
        "Write a short briefing for this collection window. "
        "Do not invent items that are not listed. "
        'Return JSON {"headline": str, "bullets": [str], "what_to_skip": [str]}.\n\n'
        f"Glance:\n{json.dumps(list(glance.bullets), ensure_ascii=False)}\n"
        f"High-relevance titles:\n{json.dumps(titles, ensure_ascii=False)}\n"
        f"Coverage clusters:\n{json.dumps(clusters, ensure_ascii=False)}\n"
    )


def apply_synthesis(
    report: DigestReport,
    *,
    allow_llm: bool = False,
    complete=None,
) -> DigestReport:
    glance = build_glance(report)
    if glance is None:
        return report
    synthesis = glance
    if allow_llm and complete is not None:
        try:
            raw = complete(build_narrative_prompt(report, glance))
            narrative = parse_narrative(raw)
            if narrative is not None:
                synthesis = narrative
        except Exception as exc:
            logger.warning("Briefing synthesis LLM failed: %s", exc)
            synthesis = replace(glance, degraded=True)
    return replace(report, briefing_synthesis=synthesis)
