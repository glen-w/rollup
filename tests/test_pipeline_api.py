"""Public pipeline import surface stays on rollup.pipeline after the package split."""

from __future__ import annotations

from rollup import pipeline
from rollup.pipeline import (
    DEFAULT_DEGRADATION_POLICY,
    EXIT_FAILURE,
    EXIT_PARTIAL,
    EXIT_SUCCESS,
    AggregatedResults,
    DigestRunResult,
    ParseCounts,
    ParseResult,
    StageWarning,
    derive_run_status,
    evaluate_no_input,
    merge_linkedin_parse,
    merge_parse,
    merge_reddit_parse,
    merge_webpage_parse,
    run_digest,
    stage_parse,
    stage_parse_mbox,
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
    assert run_digest.__module__ == "rollup.pipeline"
    assert stage_parse is stage_parse_mbox
    assert merge_linkedin_parse is pipeline.merge_linkedin_parse


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


def test_merge_parse_is_shared_by_transport_wrappers() -> None:
    prior = ParseResult(messages=(), counts=ParseCounts(messages_seen=2, messages_parsed=2))
    warning = StageWarning(code="transport_dry_run", message="would fetch")
    via_generic = merge_parse(prior, [], [warning])
    assert merge_linkedin_parse(prior, [], [warning]) == via_generic
    assert merge_reddit_parse(prior, [], [warning]) == via_generic
    assert merge_webpage_parse(prior, [], [warning]) == via_generic
    assert via_generic.counts.messages_seen == 2
    assert via_generic.warnings == (warning,)
