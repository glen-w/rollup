"""Manifest schema, privacy allowlist, and failure-manifest tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.clock import FixedClock
from rollup.config import Config
from rollup.manifest import (
    MANIFEST_SCHEMA_VERSION,
    ManifestBuilder,
    ManifestValidationError,
    config_fingerprint,
    filter_allowlisted,
    read_latest_manifest,
    validate_manifest,
)
from rollup.pipeline import AggregatedResults
from rollup.run_context import RunContext
from rollup.run_options import GroupingConfig, ManifestConfig, RunOptions


def _minimal_config(tmp_path: Path) -> Config:
    return Config(
        root=tmp_path / "root",
        mail_root=tmp_path / "mail",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        lookback_days=7,
        folders_include=(),
        folders_exclude=(),
        dry_run=False,
        no_ollama=True,
        include_seen_undated=False,
        rebuild_summaries=False,
        max_body_chars=200_000,
        max_chars_for_llm=30_000,
        max_display_links=8,
        ollama_url="http://localhost:11434/api/generate",
        ollama_model="llama3.2:3b",
        allow_remote_ollama=False,
        summary_profile=None,
        summary_variants=(),
        summary_type_routing=None,
        summary_profile_set_path=None,
        export_summary_profile_set_path=None,
        list_summary_profiles=False,
        list_newsletter_types=False,
        summary_routing_report=False,
        verbose=False,
        quiet=False,
    )


def _valid_payload() -> dict:
    return {
        "schema_version": 1,
        "run_id": "11111111-2222-3333-4444-555555555555",
        "started_at": "2026-07-10T09:00:00+00:00",
        "completed_at": "2026-07-10T09:01:00+00:00",
        "status": "success",
        "mode": "manual",
        "rollup_version": "0.2.0",
        "counts": {
            "folders_scanned": 1,
            "messages_seen": 10,
            "messages_parsed": 9,
            "messages_included": 3,
            "messages_skipped_outside_window": 5,
            "messages_skipped_seen_undated": 0,
            "messages_deduped": 1,
            "parse_fatal_errors": 1,
            "parse_anomalies": 0,
            "groups_created": 0,
            "messages_in_groups": 0,
            "standalone_cards": 3,
        },
        "outputs_published": True,
        "latest_outputs_updated": False,
    }


def test_validate_manifest_ok() -> None:
    validate_manifest(_valid_payload())


def test_validate_manifest_missing_required() -> None:
    data = _valid_payload()
    del data["run_id"]
    with pytest.raises(ManifestValidationError, match="Missing required"):
        validate_manifest(data)


def test_validate_manifest_newer_version() -> None:
    data = _valid_payload()
    data["schema_version"] = MANIFEST_SCHEMA_VERSION + 1
    with pytest.raises(ManifestValidationError, match="newer"):
        validate_manifest(data)


def test_validate_manifest_invariants() -> None:
    data = _valid_payload()
    data["counts"]["messages_parsed"] = 20
    data["counts"]["messages_seen"] = 10
    with pytest.raises(ManifestValidationError, match="messages_parsed"):
        validate_manifest(data)


def test_allowlist_strips_forbidden_keys() -> None:
    data = _valid_payload()
    data["subject"] = "secret subject"
    data["body_text"] = "secret body"
    data["message_id"] = "mid:abc"
    filtered = filter_allowlisted(data)
    assert "subject" not in filtered
    assert "body_text" not in filtered
    assert "message_id" not in filtered
    assert "run_id" in filtered


def test_manifest_builder_writes_failure_and_skips_latest(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path)
    (tmp_path / "root").mkdir()
    (tmp_path / "mail").mkdir()
    clock = FixedClock(datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc))
    ctx = RunContext.create(mode="manual", clock=clock)
    builder = ManifestBuilder(
        ctx,
        config=config,
        run_options=RunOptions(mode="manual", write_manifest=True),
        grouping=GroupingConfig(enabled=False),
        manifest_config=ManifestConfig(manifest_dir=tmp_path / "state" / "manifests"),
        window_start=datetime(2026, 7, 3, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    builder.record_failure(RuntimeError("boom"))
    builder.finalize(status="failure", aggregated=AggregatedResults(hard_failure=True))
    path = builder.write_if_state_writable(update_latest=False)
    assert path is not None
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "failure"
    assert payload["errors"]
    assert not (tmp_path / "state" / "manifests" / "latest.json").exists()


def test_validate_manifest_v1_still_readable() -> None:
    data = _valid_payload()
    assert data["schema_version"] == 1
    validate_manifest(data)


def test_manifest_builder_emits_v2_telemetry(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path)
    config = Config(**{**config.__dict__, "final_review_enabled": True, "final_review_mode": "apply"})
    (tmp_path / "root").mkdir()
    (tmp_path / "mail").mkdir()
    clock = FixedClock(datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc))
    ctx = RunContext.create(mode="manual", clock=clock)
    builder = ManifestBuilder(
        ctx,
        config=config,
        run_options=RunOptions(mode="manual", write_manifest=True),
        grouping=GroupingConfig(enabled=False),
        manifest_config=ManifestConfig(
            manifest_dir=tmp_path / "state" / "manifests",
            schema_version=MANIFEST_SCHEMA_VERSION,
        ),
        window_start=datetime(2026, 7, 3, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    agg = AggregatedResults(
        apply_global_skip_reason="fingerprint_missing",
        apply_patches_attempted=2,
        apply_patches_applied=0,
        apply_reject_counts={"missing_issue_id": 1},
        contains_auto_edited_prose=False,
        apply_policy_unattended=True,
        apply_policy_max_patches=5,
        apply_policy_max_changed_chars=800,
    )
    builder.finalize(status="partial", aggregated=agg)
    path = builder.write_if_state_writable(update_latest=False)
    assert path is not None
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert payload["final_review"]["apply_global_skip_reason"] == "fingerprint_missing"
    assert payload["final_review"]["patches_applied"] == 0
    assert payload["final_review"]["patch_reject_counts"]["missing_issue_id"] == 1


def test_manifest_builder_group_summaries_block(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path)
    config = Config(**{**config.__dict__, "group_summaries_enabled": True, "no_ollama": False})
    (tmp_path / "root").mkdir()
    (tmp_path / "mail").mkdir()
    clock = FixedClock(datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc))
    ctx = RunContext.create(mode="cron", clock=clock)
    builder = ManifestBuilder(
        ctx,
        config=config,
        run_options=RunOptions(mode="cron", cron=True, write_manifest=True),
        grouping=GroupingConfig(enabled=True),
        manifest_config=ManifestConfig(
            manifest_dir=tmp_path / "state" / "manifests",
            schema_version=2,
        ),
        window_start=datetime(2026, 7, 3, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    agg = AggregatedResults(
        group_summaries_degraded=True,
        group_summary_ollama_calls=3,
        group_summary_cache_hits=1,
        group_summary_stream_failures=2,
        group_summary_cache_write_errors=1,
        group_summary_error_counts={"cache_write_error": 1},
        usable_digest=True,
    )
    builder.finalize(status="partial", aggregated=agg)
    path = builder.write_if_state_writable(update_latest=False)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["group_summaries"]["degraded"] is True
    assert payload["group_summaries"]["ollama_calls"] == 3
    assert payload["group_summaries"]["cache_write_errors"] == 1

    config = _minimal_config(tmp_path)
    opts = RunOptions()
    grouping = GroupingConfig()
    a = config_fingerprint(config, opts, grouping)
    b = config_fingerprint(config, opts, grouping)
    assert a == b
    assert len(a) == 64
