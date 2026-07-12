"""Source policy helpers (layers on top of resolve_summary_plan)."""

from __future__ import annotations

from dataclasses import replace

from rollup.models import ClassifiedMessage, NewsletterType
from rollup.source_models import (
    CADENCE_INFERENCE_MIN_CONFIDENCE,
    CADENCE_INFERENCE_MIN_SAMPLES,
    GROUPING_POLICIES,
    CadenceLabel,
    GroupingPolicy,
    SourceObservation,
    SourceOverrides,
    SourcePolicy,
    SourcePolicyProvenance,
)


def resolve_source_policy(
    source_key: str,
    observation: SourceObservation | None,
    overrides: SourceOverrides | None,
    *,
    lifecycle: str = "active",
) -> SourcePolicy:
    """Resolve stored/inferred SourcePolicy (not message-dependent type/profile)."""
    observation = observation or SourceObservation()
    overrides = overrides or SourceOverrides()
    corrupt: list[str] = []
    prov = SourcePolicyProvenance()

    enabled = True
    if overrides.enabled is not None:
        enabled = bool(overrides.enabled)
        prov = replace(prov, enabled="user")

    always_surface = False
    if overrides.always_surface is not None:
        always_surface = bool(overrides.always_surface)
        prov = replace(prov, always_surface="user")
    if not enabled:
        always_surface = False

    priority = 0
    if overrides.priority is not None:
        if 0 <= int(overrides.priority) <= 100:
            priority = int(overrides.priority)
            prov = replace(prov, priority="user")
        else:
            corrupt.append("priority")

    newsletter_type_override: NewsletterType | None = None
    if overrides.newsletter_type is not None:
        newsletter_type_override = overrides.newsletter_type
        prov = replace(prov, newsletter_type="user")

    grouping_policy: GroupingPolicy = "auto"
    if overrides.grouping_policy is not None:
        if overrides.grouping_policy in GROUPING_POLICIES:
            grouping_policy = overrides.grouping_policy
            prov = replace(prov, grouping_policy="user")
        else:
            corrupt.append("grouping_policy")
    else:
        inferred = _infer_grouping(observation)
        if inferred is not None:
            grouping_policy = inferred
            prov = replace(prov, grouping_policy="inferred")

    summary_profile_override = None
    if overrides.summary_profile:
        summary_profile_override = overrides.summary_profile
        prov = replace(prov, summary_profile="user")

    expected_cadence: CadenceLabel = "unknown"
    if overrides.expected_cadence is not None:
        expected_cadence = overrides.expected_cadence
        prov = replace(prov, expected_cadence="user")
    elif observation.cadence.label != "unknown":
        expected_cadence = observation.cadence.label
        prov = replace(prov, expected_cadence="observed")

    display_name_override = overrides.display_name
    if display_name_override:
        prov = replace(prov, display_name="user")

    return SourcePolicy(
        source_key=source_key,
        enabled=enabled,
        always_surface=always_surface,
        priority=priority,
        newsletter_type_override=newsletter_type_override,
        grouping_policy=grouping_policy,
        summary_profile_override=summary_profile_override,
        expected_cadence=expected_cadence,
        display_name_override=display_name_override,
        display_name_observed=observation.display_name_observed,
        notes=overrides.notes,
        lifecycle="superseded" if lifecycle == "superseded" else "active",
        provenance=prov,
        corrupt_fields=tuple(corrupt),
    )


def _infer_grouping(observation: SourceObservation) -> GroupingPolicy | None:
    cad = observation.cadence
    if (
        cad.confidence < CADENCE_INFERENCE_MIN_CONFIDENCE
        or cad.sample_count < CADENCE_INFERENCE_MIN_SAMPLES
    ):
        return None
    if cad.label == "realtime":
        return "notification_stream"
    if cad.label in ("daily", "several_per_week", "weekly"):
        return "sender_batch"
    return None


def apply_effective_type(
    classified: ClassifiedMessage,
    policy: SourcePolicy | None,
) -> tuple[ClassifiedMessage, NewsletterType, NewsletterType, bool]:
    """Return (classified_with_effective_type, detected, effective, disagreed)."""
    detected: NewsletterType = classified.newsletter_type
    effective = detected
    if policy and policy.newsletter_type_override:
        effective = policy.newsletter_type_override
    disagreed = effective != detected
    if effective == detected:
        return classified, detected, effective, False
    return (
        replace(classified, newsletter_type=effective),
        detected,
        effective,
        True,
    )


def resolve_display_name(policy: SourcePolicy | None, sender_fallback: str) -> str:
    if policy and policy.display_name_override:
        return policy.display_name_override
    if policy and policy.display_name_observed:
        return policy.display_name_observed
    return sender_fallback


def priority_sort_prefix(priority: int) -> tuple[int, ...]:
    """Primary sort key: higher priority first."""
    return (-int(priority),)


def group_priority(policies: list[SourcePolicy | None]) -> int:
    """Single-source → that priority; else max member priority."""
    vals = [p.priority for p in policies if p is not None]
    if not vals:
        return 0
    if len(vals) == 1:
        return vals[0]
    return max(vals)
