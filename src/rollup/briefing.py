"""Briefing-layer helpers: inclusion flattening for attention/coverage/synthesis.

Ranking, clustering, and synthesis may reorder or annotate entries. They must
never change the set of ``message_key`` values that passed filter.
"""

from __future__ import annotations

from collections.abc import Iterable

from rollup.models import DigestEntry, DigestGroup, DigestItem, DigestReport


def iter_digest_entries(items: Iterable[DigestItem]) -> tuple[DigestEntry, ...]:
    """Flatten standalone entries and group members, preserving encounter order."""
    out: list[DigestEntry] = []
    for item in items:
        if isinstance(item, DigestGroup):
            out.extend(item.entries)
        else:
            out.append(item)
    return tuple(out)


def included_message_keys(items: Iterable[DigestItem]) -> tuple[str, ...]:
    """Ordered ``message_key`` values for every entry in *items* (groups expanded)."""
    return tuple(
        entry.classified.parsed.message_key for entry in iter_digest_entries(items)
    )


def report_included_message_keys(report: DigestReport) -> frozenset[str]:
    """Set of every included ``message_key`` in dated folders and undated."""
    keys: list[str] = []
    for folder_items in report.dated_by_folder.values():
        keys.extend(included_message_keys(folder_items))
    keys.extend(included_message_keys(report.undated))
    return frozenset(keys)


def inclusion_preserved(before: DigestReport, after: DigestReport) -> bool:
    """True when *after* contains exactly the same message keys as *before*."""
    return report_included_message_keys(before) == report_included_message_keys(after)
