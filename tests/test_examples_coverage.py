"""Ensure docs/EXAMPLES.md documents major CLI capabilities."""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
EXAMPLES_PATH = PROJECT_ROOT / "docs" / "EXAMPLES.md"
README_PATH = PROJECT_ROOT / "README.md"

# Each entry is (capability label, required substring in EXAMPLES.md).
REQUIRED_EXAMPLE_COVERAGE: tuple[tuple[str, str], ...] = (
    ("inventory command", "rollup inventory"),
    ("digest command", "rollup digest"),
    ("doctor command", "rollup doctor"),
    ("cron print-launchd", "cron print-launchd"),
    ("cron status", "cron status"),
    ("cron mode", "--cron"),
    ("grouping report", "--grouping-report"),
    ("no grouping", "--no-grouping"),
    ("synthetic fixtures root", "tests/fixtures/Newsletters.sbd"),
    ("no-ollama mode", "--no-ollama"),
    ("ollama mode", "--ollama"),
    ("dry-run", "--dry-run"),
    ("folder filter", "--folder"),
    ("exclude folder", "--exclude-folder"),
    ("include seen undated", "--include-seen-undated"),
    ("lookback days", "--lookback-days"),
    ("inventory json output", "--json-out"),
    ("list summary profiles", "--list-summary-profiles"),
    ("list newsletter types", "--list-newsletter-types"),
    ("summary profile", "--summary-profile"),
    ("summary type routing", "--summary-type-routing"),
    ("no summary type routing", "--no-summary-type-routing"),
    ("summary variants", "--summary-variants"),
    ("export summary profile set", "--export-summary-profile-set"),
    ("summary profile set", "--summary-profile-set"),
    ("summary routing report", "--summary-routing-report"),
    ("rebuild summaries", "--rebuild-summaries"),
    ("final review", "--final-review"),
    ("final review profile", "--final-review-profile"),
    ("final review report", "--final-review-report"),
    ("no final review cache", "--no-final-review-cache"),
    ("final review mode apply", "--final-review-mode apply"),
    ("final review allow cron apply", "--final-review-allow-cron-apply"),
    ("group summaries", "--group-summaries"),
    ("web command", "rollup web"),
    ("web reindex", "rollup web reindex"),
    ("benchmark script", "scripts/benchmark_ollama_models.py"),
    ("regenerate fixtures", "tests/generate_fixtures.py"),
)


def _examples_text() -> str:
    if not EXAMPLES_PATH.is_file():
        pytest.fail(f"Missing examples doc: {EXAMPLES_PATH}")
    return EXAMPLES_PATH.read_text(encoding="utf-8")


def test_examples_doc_exists() -> None:
    assert EXAMPLES_PATH.is_file()


def test_readme_links_to_examples_doc() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    assert "docs/EXAMPLES.md" in readme


@pytest.mark.parametrize(
    ("capability", "required"),
    REQUIRED_EXAMPLE_COVERAGE,
    ids=[label for label, _ in REQUIRED_EXAMPLE_COVERAGE],
)
def test_examples_doc_covers_capability(capability: str, required: str) -> None:
    text = _examples_text()
    assert required in text, (
        f"docs/EXAMPLES.md is missing coverage for {capability!r}; "
        f"expected substring {required!r}."
    )
