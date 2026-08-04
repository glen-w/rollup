"""Serialize DigestReport to structured JSON (full rollup, no raw bodies)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from rollup.addons.artifact_write import atomic_write_digest_artifact
from rollup.final_review import final_review_result_to_dict
from rollup.links import prepare_links_for_render
from rollup.models import (
    ClassifiedLink,
    DigestEntry,
    DigestGroup,
    DigestItem,
    DigestReport,
    LinkItem,
)

SCHEMA_VERSION = 1
FORMAT_NAME = "rollup.digest"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _link_to_dict(link: ClassifiedLink) -> dict[str, Any]:
    return {
        "href": link.href,
        "text": link.text,
        "context": link.context,
        "source_index": link.source_index,
        "label": link.label,
        "domain": link.domain,
        "category": link.category,
        "priority": link.priority,
        "is_main": link.is_main,
        "hidden_reason": link.hidden_reason,
        "dedupe_key": link.dedupe_key,
    }


def _entry_links(
    entry: DigestEntry, max_display_links: int
) -> dict[str, list[dict[str, Any]]]:
    p = entry.classified.parsed
    link_items = (
        list(p.link_items)
        if getattr(p, "link_items", ())
        else [
            LinkItem(href=href, text=None, context=None, source_index=index)
            for index, href in enumerate(p.links)
        ]
    )
    max_main = min(5, max_display_links)
    max_other = max(0, max_display_links - max_main)
    bundle = prepare_links_for_render(
        link_items, max_main=max_main, max_other=max_other
    )
    return {
        "main_links": [_link_to_dict(link) for link in bundle.main_links],
        "other_links": [_link_to_dict(link) for link in bundle.other_links],
        "hidden_links": [_link_to_dict(link) for link in bundle.hidden_links],
    }


def entry_to_dict(entry: DigestEntry, max_display_links: int) -> dict[str, Any]:
    """Serialize one digest entry without body_html / body_text."""
    p = entry.classified.parsed
    payload: dict[str, Any] = {
        "kind": "entry",
        "message_key": p.message_key,
        "content_hash": p.content_hash,
        "source_key": p.source_key,
        "list_id": p.list_id,
        "subject": p.subject,
        "sender": p.sender,
        "date_raw": p.date_raw,
        "date_parsed": _iso(p.date_parsed),
        "folder_name": p.folder_name,
        "relative_folder_path": p.relative_folder_path,
        "newsletter_type": entry.classified.newsletter_type,
        "classification_scores": [
            {"type": ntype, "score": score}
            for ntype, score in entry.classified.classification_scores
        ],
        "read_time_minutes": p.read_time_minutes,
        "preview": p.preview,
        "summary": entry.summary,
        "summary_source": entry.summary_source,
        "summary_original": entry.summary_original,
        "parse_warnings": list(p.parse_warnings),
        "html_heading_count": p.html_heading_count,
        "html_link_count": p.html_link_count,
        "html_section_break_count": p.html_section_break_count,
    }
    payload.update(_entry_links(entry, max_display_links))
    return payload


def group_to_dict(group: DigestGroup, max_display_links: int) -> dict[str, Any]:
    return {
        "kind": "group",
        "group_id": group.group_id,
        "group_type": group.group_type,
        "display_name": group.display_name,
        "sender_normalized": group.sender_normalized,
        "folder_name": group.folder_name,
        "render_mode": group.render_mode,
        "group_summary": group.group_summary,
        "group_summary_source": group.group_summary_source,
        "entries": [
            entry_to_dict(entry, max_display_links) for entry in group.entries
        ],
    }


def item_to_dict(item: DigestItem, max_display_links: int) -> dict[str, Any]:
    if isinstance(item, DigestGroup):
        return group_to_dict(item, max_display_links)
    return entry_to_dict(item, max_display_links)


def _stats_to_dict(report: DigestReport) -> dict[str, Any]:
    s = report.stats
    return {
        "folders_scanned": s.folders_scanned,
        "messages_parsed": s.messages_parsed,
        "dated_included": s.dated_included,
        "undated_needing_review": s.undated_needing_review,
        "skipped_outside_window": s.skipped_outside_window,
        "skipped_seen_undated": s.skipped_seen_undated,
        "deduped_messages": s.deduped_messages,
        "parse_errors": s.parse_errors,
        "summaries_ollama": s.summaries_ollama,
        "summaries_cache": s.summaries_cache,
        "summaries_fallback": s.summaries_fallback,
        "summaries_errors": s.summaries_errors,
    }


def _summary_metadata_to_dict(report: DigestReport) -> dict[str, Any] | None:
    metadata = report.summary_metadata
    if metadata is None:
        return None
    return {
        "mode": metadata.mode,
        "profiles_used": list(metadata.profiles_used),
        "models_used": list(metadata.models_used),
        "summaries_ollama": metadata.summaries_ollama,
        "summaries_cache": metadata.summaries_cache,
        "summaries_fallback": metadata.summaries_fallback,
        "summaries_errors": metadata.summaries_errors,
        "selected_profiles": list(metadata.selected_profiles),
        "output_variants": list(metadata.output_variants),
        "routing_counts": [
            {
                "newsletter_type": row.newsletter_type,
                "profile_name": row.profile_name,
                "model": row.model,
                "count": row.count,
            }
            for row in metadata.routing_counts
        ],
        "anomaly_rows": [
            {
                "subject": row.subject,
                "profile_name": row.profile_name,
                "status": row.status,
                "stop_reason": row.stop_reason,
                "output_chars": row.output_chars,
                "elapsed_seconds": row.elapsed_seconds,
                "cached": row.cached,
            }
            for row in metadata.anomaly_rows
        ],
        "variant_name": metadata.variant_name,
    }


def _grouping_metadata_to_dict(report: DigestReport) -> dict[str, Any] | None:
    metadata = report.grouping_metadata
    if metadata is None:
        return None
    return {
        "groups_created": metadata.groups_created,
        "messages_in_groups": metadata.messages_in_groups,
        "standalone_cards": metadata.standalone_cards,
        "grouping_counts": dict(metadata.grouping_counts),
    }


def _group_summary_metadata_to_dict(report: DigestReport) -> dict[str, Any] | None:
    metadata = report.group_summary_metadata
    if metadata is None:
        return None
    return {
        "groups_attempted": metadata.groups_attempted,
        "groups_succeeded": metadata.groups_succeeded,
        "groups_failed": metadata.groups_failed,
        "groups_skipped_budget": metadata.groups_skipped_budget,
        "ollama_calls": metadata.ollama_calls,
        "cache_hits": metadata.cache_hits,
        "errors": metadata.errors,
        "degraded": metadata.degraded,
        "error_counts": [
            {"reason": reason, "count": count}
            for reason, count in metadata.error_counts
        ],
        "cache_write_errors": metadata.cache_write_errors,
        "stream_failures": metadata.stream_failures,
    }


def report_to_dict(report: DigestReport, max_display_links: int) -> dict[str, Any]:
    """Full structured rollup payload (schema v1)."""
    dated: dict[str, list[dict[str, Any]]] = {}
    for folder, items in sorted(report.dated_by_folder.items()):
        dated[folder] = [item_to_dict(item, max_display_links) for item in items]
    return {
        "schema_version": SCHEMA_VERSION,
        "format": FORMAT_NAME,
        "generated_at": _iso(report.generated_at),
        "lookback_days": report.lookback_days,
        "window_start": _iso(report.window_start),
        "window_end": _iso(report.window_end),
        "stats": _stats_to_dict(report),
        "summary_metadata": _summary_metadata_to_dict(report),
        "grouping_metadata": _grouping_metadata_to_dict(report),
        "group_summary_metadata": _group_summary_metadata_to_dict(report),
        "final_review": (
            final_review_result_to_dict(report.final_review)
            if report.final_review is not None
            else None
        ),
        "dated_by_folder": dated,
        "undated": [
            item_to_dict(item, max_display_links) for item in report.undated
        ],
    }


def render_json(report: DigestReport, max_display_links: int) -> str:
    """Pretty-printed JSON text for the digest artifact."""
    payload = report_to_dict(report, max_display_links)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def atomic_write_json_digest(
    output_dir: Path,
    generated_at: datetime,
    text: str,
    *,
    run_id_short: str | None = None,
) -> Path:
    """Write structured JSON beside the core stem (``.json``)."""
    return atomic_write_digest_artifact(
        output_dir,
        generated_at,
        text,
        extension="json",
        run_id_short=run_id_short,
    )
