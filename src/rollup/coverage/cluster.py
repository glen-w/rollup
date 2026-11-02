"""Cross-source coverage clusters. Overlay only — never drops members."""

from __future__ import annotations

import hashlib
from dataclasses import replace

from rollup.attention.bands import band_rank
from rollup.attention.profile import jaccard
from rollup.briefing import included_message_keys, iter_digest_entries
from rollup.coverage.normalize import entry_main_urls, title_tokens
from rollup.models import CoverageCluster, DigestEntry, DigestGroup, DigestItem

TITLE_JACCARD_MIN = 0.45
MIN_CLUSTER_SIZE = 2
MAX_CLUSTERS = 24


class _UnionFind:
    def __init__(self, keys: list[str]) -> None:
        self.parent = {k: k for k in keys}

    def find(self, key: str) -> str:
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _cluster_id(member_keys: tuple[str, ...]) -> str:
    blob = "|".join(sorted(member_keys)).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _theme_label(entries: list[DigestEntry]) -> str:
    domains: list[str] = []
    for entry in entries:
        for url in entry_main_urls(entry):
            host = url.split("/")[2] if "://" in url else ""
            if host:
                domains.append(host)
    if domains:
        best = max(set(domains), key=domains.count)
        return best
    tokens: list[str] = []
    for entry in entries:
        tokens.extend(sorted(title_tokens(entry.classified.parsed.subject)))
    if tokens:
        return " ".join(list(dict.fromkeys(tokens))[:6])
    return entries[0].classified.parsed.subject[:80]


def _pick_representative(entries: list[DigestEntry]) -> DigestEntry:
    def key(entry: DigestEntry) -> tuple:
        parsed = entry.classified.parsed
        long_form = 1 if parsed.read_time_minutes >= 8 else 0
        essay = 1 if entry.classified.newsletter_type == "essay" else 0
        return (
            -band_rank(entry.attention_band),
            essay,
            long_form,
            len(parsed.body_text or parsed.preview or ""),
            parsed.message_key,
        )

    return max(entries, key=key)


def cluster_entries(
    entries: tuple[DigestEntry, ...],
) -> tuple[CoverageCluster, ...]:
    if len(entries) < MIN_CLUSTER_SIZE:
        return ()
    keys = [e.classified.parsed.message_key for e in entries]
    by_key = {e.classified.parsed.message_key: e for e in entries}
    uf = _UnionFind(keys)
    urls_by_key = {k: set(entry_main_urls(by_key[k])) for k in keys}
    tokens_by_key = {
        k: title_tokens(by_key[k].classified.parsed.subject) for k in keys
    }
    url_index: dict[str, list[str]] = {}
    for key, urls in urls_by_key.items():
        for url in urls:
            url_index.setdefault(url, []).append(key)
    for group in url_index.values():
        head = group[0]
        for other in group[1:]:
            uf.union(head, other)
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            if uf.find(a) == uf.find(b):
                continue
            shared = bool(urls_by_key[a] & urls_by_key[b])
            jac = jaccard(tokens_by_key[a], tokens_by_key[b])
            if jac >= TITLE_JACCARD_MIN and (shared or jac >= 0.7):
                uf.union(a, b)
    buckets: dict[str, list[str]] = {}
    for key in keys:
        buckets.setdefault(uf.find(key), []).append(key)
    clusters: list[CoverageCluster] = []
    for members in buckets.values():
        if len(members) < MIN_CLUSTER_SIZE:
            continue
        member_entries = [by_key[k] for k in members]
        sources = {
            e.classified.parsed.source_key or e.classified.parsed.sender
            for e in member_entries
        }
        rep = _pick_representative(member_entries)
        ordered = tuple(sorted(members))
        clusters.append(
            CoverageCluster(
                cluster_id=_cluster_id(ordered),
                theme_label=_theme_label(member_entries),
                member_keys=ordered,
                representative_key=rep.classified.parsed.message_key,
                source_count=len(sources),
            )
        )
    clusters.sort(key=lambda c: (-c.source_count, -len(c.member_keys), c.cluster_id))
    return tuple(clusters[:MAX_CLUSTERS])


def apply_clusters_to_items(
    items: tuple[DigestItem, ...],
    clusters: tuple[CoverageCluster, ...],
) -> tuple[DigestItem, ...]:
    by_member = {
        key: cluster.cluster_id
        for cluster in clusters
        for key in cluster.member_keys
    }

    def _one(entry: DigestEntry) -> DigestEntry:
        cid = by_member.get(entry.classified.parsed.message_key)
        if cid is None:
            return entry
        return replace(entry, coverage_cluster_id=cid)

    out: list[DigestItem] = []
    for item in items:
        if isinstance(item, DigestGroup):
            out.append(replace(item, entries=tuple(_one(e) for e in item.entries)))
        else:
            out.append(_one(item))
    annotated = tuple(out)
    if included_message_keys(items) != included_message_keys(annotated):
        raise RuntimeError("coverage clustering dropped or added message keys")
    return annotated


def apply_theme_overrides(
    clusters: tuple[CoverageCluster, ...],
    labels: dict[str, str],
) -> tuple[CoverageCluster, ...]:
    if not labels:
        return clusters
    return tuple(
        replace(cluster, theme_label=labels[cluster.cluster_id])
        if cluster.cluster_id in labels and labels[cluster.cluster_id].strip()
        else cluster
        for cluster in clusters
    )


def format_coverage_report(clusters: tuple[CoverageCluster, ...]) -> str:
    if not clusters:
        return "Coverage: 0 clusters"
    lines = [f"Coverage: {len(clusters)} cluster(s)"]
    for cluster in clusters:
        lines.append(
            f"  {cluster.cluster_id} n={len(cluster.member_keys)} "
            f"sources={cluster.source_count} {cluster.theme_label}"
        )
    return "\n".join(lines)


def cluster_for_entry(
    entry: DigestEntry, clusters: tuple[CoverageCluster, ...]
) -> CoverageCluster | None:
    cid = entry.coverage_cluster_id
    if not cid:
        return None
    for cluster in clusters:
        if cluster.cluster_id == cid:
            return cluster
    return None
