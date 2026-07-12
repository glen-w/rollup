"""Exit / degradation semantics for hardening telemetry."""

from __future__ import annotations

from rollup.pipeline import AggregatedResults, derive_run_status, status_to_exit_code


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
