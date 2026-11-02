"""Public pipeline import surface stays on rollup.pipeline after the package split."""

from __future__ import annotations

from rollup import pipeline
from rollup.pipeline import (
    EXIT_FAILURE,
    EXIT_PARTIAL,
    EXIT_SUCCESS,
    AggregatedResults,
    DEFAULT_DEGRADATION_POLICY,
    DigestRunResult,
    ParseCounts,
    ParseResult,
    derive_run_status,
    evaluate_no_input,
    run_digest,
    status_to_exit_code,
)
from rollup.pipeline import status as pipeline_status
from rollup.pipeline import types as pipeline_types


def test_public_names_are_package_reexports() -> None:
    assert AggregatedResults is pipeline_types.AggregatedResults
    assert DigestRunResult is pipeline_types.DigestRunResult
    assert DEFAULT_DEGRADATION_POLICY is pipeline_types.DEFAULT_DEGRADATION_POLICY
    assert derive_run_status is pipeline_status.derive_run_status
    assert evaluate_no_input is pipeline_status.evaluate_no_input
    assert status_to_exit_code is pipeline_status.status_to_exit_code
    assert EXIT_SUCCESS is pipeline_status.EXIT_SUCCESS
    assert EXIT_PARTIAL == 2
    assert EXIT_FAILURE == 1
    assert run_digest is pipeline.run_digest


def test_aggregated_results_aliases() -> None:
    agg = AggregatedResults(usable_digest=True, ollama_enabled=True, dated_outputs_written=True)
    assert agg.llm_enabled is True
    assert agg.outputs_published is True
    agg.outputs_published = False
    assert agg.dated_outputs_written is False
    assert derive_run_status(agg) == "success"
    assert status_to_exit_code("partial") == EXIT_PARTIAL


def test_evaluate_no_input_empty_discovery() -> None:
    from rollup.pipeline.types import DiscoveryResult

    discovery = DiscoveryResult(folders=())
    parse = ParseResult(messages=(), counts=ParseCounts())
    reason = evaluate_no_input(
        folders_include=(),
        discovery=discovery,
        parse=parse,
    )
    assert reason is not None
    assert "No readable mbox folders" in reason
