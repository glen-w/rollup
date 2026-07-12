"""Tests for source policy resolution layers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rollup.filter import make_digest_entry
from rollup.models import ClassifiedMessage, ParsedMessage
from rollup.source_cadence import estimate_cadence
from rollup.source_models import (
    CadenceEstimate,
    SourceObservation,
    SourceOverrides,
)
from rollup.source_policy import apply_effective_type, resolve_source_policy
from rollup.summary_plan import SummaryCliOptions, resolve_summary_plan
from rollup.summary_profiles import get_builtin_summary_profile_set


def _obs(**kwargs) -> SourceObservation:
    return SourceObservation(**kwargs)


def test_disable_outranks_always_surface():
    policy = resolve_source_policy(
        "from:a@b.co",
        _obs(),
        SourceOverrides(enabled=False, always_surface=True),
    )
    assert policy.enabled is False
    assert policy.always_surface is False


def test_user_grouping_beats_inference():
    obs = _obs(
        cadence=CadenceEstimate("daily", 0.9, 10, 24.0),
    )
    policy = resolve_source_policy(
        "from:a@b.co",
        obs,
        SourceOverrides(grouping_policy="standalone"),
    )
    assert policy.grouping_policy == "standalone"
    assert policy.provenance.grouping_policy == "user"


def test_inferred_grouping_gated():
    obs = _obs(cadence=CadenceEstimate("daily", 0.9, 10, 24.0))
    policy = resolve_source_policy("from:a@b.co", obs, SourceOverrides())
    assert policy.grouping_policy == "sender_batch"
    assert policy.provenance.grouping_policy == "inferred"

    weak = _obs(cadence=CadenceEstimate("daily", 0.5, 10, 24.0))
    policy2 = resolve_source_policy("from:a@b.co", weak, SourceOverrides())
    assert policy2.grouping_policy == "auto"


def test_effective_type_keeps_detected():
    parsed = ParsedMessage(
        message_key="mid:1",
        content_hash="abc",
        folder_name="tech",
        relative_folder_path="tech",
        subject="Hi",
        sender="A <a@b.co>",
        date_raw="",
        date_parsed=None,
        body_text="hello world " * 20,
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=1,
        preview="hello",
        parse_warnings=(),
        source_key="from:a@b.co",
    )
    classified = ClassifiedMessage(
        parsed=parsed,
        newsletter_type="short_update",
        classification_scores=(("short_update", 1.0),),
    )
    policy = resolve_source_policy(
        "from:a@b.co",
        _obs(),
        SourceOverrides(newsletter_type="essay"),
    )
    new_c, detected, effective, disagreed = apply_effective_type(classified, policy)
    assert detected == "short_update"
    assert effective == "essay"
    assert disagreed is True
    assert new_c.newsletter_type == "essay"


def test_summary_precedence_source_over_cli():
    from rollup.classify import classify_message

    parsed = ParsedMessage(
        message_key="mid:1",
        content_hash="abc",
        folder_name="tech",
        relative_folder_path="tech",
        subject="Hi",
        sender="A <a@b.co>",
        date_raw="",
        date_parsed=None,
        body_text="hello world " * 50,
        body_html=None,
        html_heading_count=0,
        html_link_count=0,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=1,
        preview="hello",
        parse_warnings=(),
        source_key="from:a@b.co",
    )
    entry = make_digest_entry(classify_message(parsed), no_ollama=True)
    policy = resolve_source_policy(
        "from:a@b.co",
        _obs(),
        SourceOverrides(summary_profile="rough"),
    )
    plan = resolve_summary_plan(
        [entry],
        get_builtin_summary_profile_set(),
        SummaryCliOptions(summary_profile="standard", summary_type_routing=True),
        policy_by_message_key={entry.classified.parsed.message_key: policy},
    )
    job = plan.jobs_by_variant["default"][0]
    assert job.profile_name == "rough"


def test_cadence_sample_count_is_messages():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    dates = [base + timedelta(days=i) for i in range(5)]
    est = estimate_cadence(dates)
    assert est.sample_count == 5
    assert est.label == "daily"
