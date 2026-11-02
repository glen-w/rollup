"""Exit / degradation semantics for hardening telemetry."""

from __future__ import annotations

import pytest

from rollup.pipeline import (
    AggregatedResults,
    derive_run_status,
    format_partial_reasons,
    status_to_exit_code,
)
from rollup.pipeline import status as status_mod
from rollup import run_contracts


def test_group_summaries_degraded_is_partial() -> None:
    agg = AggregatedResults(
        usable_digest=True,
        group_summaries_degraded=True,
    )
    status = derive_run_status(agg)
    assert status == "partial"
    assert status_to_exit_code(status) == 2


def test_apply_skip_alone_is_not_failure() -> None:
    agg = AggregatedResults(
        usable_digest=True,
        apply_global_skip_reason="fingerprint_missing",
        apply_patches_applied=0,
    )
    assert derive_run_status(agg) == "success"


def test_exit_constants_match_run_contracts() -> None:
    assert status_mod.EXIT_SUCCESS == run_contracts.EXIT_SUCCESS == 0
    assert status_mod.EXIT_FAILURE == run_contracts.EXIT_FAILURE == 1
    assert status_mod.EXIT_PARTIAL == run_contracts.EXIT_PARTIAL == 2


@pytest.mark.parametrize(
    "flag,expected",
    [
        ("optional_writer_failed", "partial"),
        ("publication_failed", "partial"),
        ("seen_state_failed", "partial"),
        ("manifest_write_failed", "partial"),
        ("linkedin_degraded", "partial"),
        ("reddit_degraded", "partial"),
        ("webpage_degraded", "partial"),
        ("mbox_mutation_detected", "partial"),
        ("final_review_failed", "partial"),
        ("web_index_failed", "success"),
        ("required_writer_failed", "failure"),
    ],
)
def test_degradation_flag_status(flag: str, expected: str) -> None:
    agg = AggregatedResults(usable_digest=True, **{flag: True})
    assert derive_run_status(agg) == expected


def test_format_partial_reasons_lists_flags() -> None:
    agg = AggregatedResults(
        usable_digest=True,
        optional_writer_failed=True,
        reddit_degraded=True,
    )
    text = format_partial_reasons(agg)
    assert "optional writer" in text
    assert "reddit" in text
