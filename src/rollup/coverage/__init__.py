"""Cross-source coverage overlay. Same-source grouping is unchanged."""

from rollup.coverage.cluster import cluster_entries, format_coverage_report
from rollup.coverage.stage import apply_coverage

__all__ = ["apply_coverage", "cluster_entries", "format_coverage_report"]
