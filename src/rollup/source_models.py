"""Typed models for the newsletter source registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping

from rollup.models import NewsletterType

SourceLifecycle = Literal["active", "superseded"]
GroupingPolicy = Literal[
    "auto",
    "standalone",
    "notification_stream",
    "daily_editions",
    "sender_batch",
]
CadenceLabel = Literal[
    "unknown",
    "realtime",
    "daily",
    "several_per_week",
    "weekly",
    "irregular",
]
PolicyProvenance = Literal["default", "inferred", "observed", "user", "import"]
GROUPING_POLICIES = frozenset(
    {"auto", "standalone", "notification_stream", "daily_editions", "sender_batch"}
)
CADENCE_LABELS = frozenset(
    {"unknown", "realtime", "daily", "several_per_week", "weekly", "irregular"}
)
CADENCE_INFERENCE_MIN_SAMPLES = 5
CADENCE_INFERENCE_MIN_CONFIDENCE = 0.6
CADENCE_SAMPLE_RETENTION = 60


@dataclass(frozen=True)
class CadenceEstimate:
    label: CadenceLabel
    confidence: float
    sample_count: int
    median_hours: float | None


@dataclass(frozen=True)
class SourceOverrides:
    enabled: bool | None = None
    always_surface: bool | None = None
    priority: int | None = None
    newsletter_type: NewsletterType | None = None
    grouping_policy: GroupingPolicy | None = None
    summary_profile: str | None = None
    expected_cadence: CadenceLabel | None = None
    display_name: str | None = None
    notes: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class SourceObservation:
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    message_count_total: int = 0
    observed_from_addrs: tuple[str, ...] = ()
    observed_list_id: str | None = None
    last_folder_name: str | None = None
    last_detected_newsletter_type: str | None = None
    cadence: CadenceEstimate = field(
        default_factory=lambda: CadenceEstimate("unknown", 0.0, 0, None)
    )
    display_name_observed: str | None = None
    last_subject_family: str | None = None
    cadence_calculated_at: str | None = None


@dataclass(frozen=True)
class SourcePolicyProvenance:
    enabled: PolicyProvenance = "default"
    always_surface: PolicyProvenance = "default"
    priority: PolicyProvenance = "default"
    newsletter_type: PolicyProvenance = "default"
    grouping_policy: PolicyProvenance = "default"
    summary_profile: PolicyProvenance = "default"
    expected_cadence: PolicyProvenance = "default"
    display_name: PolicyProvenance = "default"


@dataclass(frozen=True)
class SourcePolicy:
    """Stored/inferred source facts — not message-dependent effective type/profile."""

    source_key: str
    enabled: bool = True
    always_surface: bool = False
    priority: int = 0
    newsletter_type_override: NewsletterType | None = None
    grouping_policy: GroupingPolicy = "auto"
    summary_profile_override: str | None = None
    expected_cadence: CadenceLabel = "unknown"
    display_name_override: str | None = None
    display_name_observed: str | None = None
    notes: str | None = None
    lifecycle: SourceLifecycle = "active"
    provenance: SourcePolicyProvenance = field(default_factory=SourcePolicyProvenance)
    corrupt_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceRegistrySnapshot:
    """Immutable post-observation registry view for one digest run."""

    policies: Mapping[str, SourcePolicy] = field(default_factory=dict)
    aliases: Mapping[str, str] = field(default_factory=dict)
    known_count: int = 0
    discovered_this_run: int = 0
    registry_schema_version: int = 7
    policy_state_revision: str = ""
    messages_unidentifiable_source: int = 0

    def policy_for(self, source_key: str | None) -> SourcePolicy | None:
        if not source_key:
            return None
        canonical = self.aliases.get(source_key, source_key)
        return self.policies.get(canonical)

    def resolve_key(self, source_key: str | None) -> str | None:
        if not source_key:
            return None
        return self.aliases.get(source_key, source_key)


def empty_defaults_snapshot(
    *,
    messages_unidentifiable_source: int = 0,
) -> SourceRegistrySnapshot:
    return SourceRegistrySnapshot(
        messages_unidentifiable_source=messages_unidentifiable_source
    )


@dataclass(frozen=True)
class SourceRecord:
    source_key: str
    observation: SourceObservation
    overrides: SourceOverrides
    policy: SourcePolicy
    lifecycle: SourceLifecycle = "active"
    superseded_by: str | None = None
