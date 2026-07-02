"""Summary planning and execution-oriented models."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Literal

from rollup.models import DigestEntry
from rollup.ollama_stream import StreamStopReason
from rollup.summary_profiles import (
    DisabledSummaryProfileError,
    SummaryProfile,
    SummaryProfileSet,
    UnknownSummaryProfileError,
)

SummaryRoutingMode = Literal[
    "fallback_no_llm", "single_profile", "type_routed", "variants"
]
SummaryResultStatus = Literal[
    "cache", "legacy_cache", "ollama", "fallback", "error", "skipped"
]


class SummaryProviderError(RuntimeError):
    """Raised when a summary provider fails."""


class SummaryModelUnavailableError(SummaryProviderError):
    """Raised when a provider model is unavailable at runtime."""


@dataclass(frozen=True)
class SummaryCliOptions:
    summary_profile: str | None = None
    summary_variants: tuple[str, ...] = ()
    summary_type_routing: bool = False


@dataclass(frozen=True)
class SummaryJob:
    message_key: str
    content_hash: str
    canonical_newsletter_type: str
    summary_input_hash: str
    profile_name: str
    prompt_style: str
    provider: str
    model: str
    options: dict[str, object]
    temperature: float
    num_ctx: int | None
    timeout_seconds: int | None
    variant_name: str


@dataclass(frozen=True)
class SummaryPlan:
    mode: SummaryRoutingMode
    selected_profiles: tuple[str, ...]
    type_routes_used: dict[str, str]
    jobs_by_variant: dict[str, tuple[SummaryJob, ...]]
    output_variants: tuple[str, ...]


@dataclass(frozen=True)
class SummaryResult:
    message_key: str
    subject: str
    newsletter_type: str
    profile_name: str
    provider: str
    model: str
    prompt_style: str
    status: SummaryResultStatus
    summary_text: str | None
    error_message: str | None
    elapsed_seconds: float | None
    input_char_count: int
    output_char_count: int
    body_chars: int
    prompt_chars: int
    link_count: int
    stop_reason: StreamStopReason | None
    cached: bool
    variant_name: str


@dataclass(frozen=True)
class SummaryAnomalyRow:
    subject: str
    profile_name: str
    status: SummaryResultStatus
    stop_reason: StreamStopReason | None
    output_chars: int
    elapsed_seconds: float | None
    cached: bool


@dataclass(frozen=True)
class SummaryPerformanceRow:
    profile_name: str
    provider: str
    model: str
    newsletter_type: str
    input_char_count: int
    output_char_count: int
    elapsed_seconds: float
    status: SummaryResultStatus
    variant_name: str


@dataclass(frozen=True)
class SummaryRoutingCount:
    newsletter_type: str
    profile_name: str
    model: str
    count: int


@dataclass(frozen=True)
class SummaryExecutionReport:
    mode: SummaryRoutingMode
    output_variants: tuple[str, ...]
    selected_profiles: tuple[str, ...]
    profiles_used: tuple[str, ...]
    models_used: tuple[str, ...]
    summaries_ollama: int
    summaries_cache: int
    summaries_fallback: int
    summaries_errors: int
    routing_counts: tuple[SummaryRoutingCount, ...]
    performance_rows: tuple[SummaryPerformanceRow, ...] = ()
    anomaly_rows: tuple[SummaryAnomalyRow, ...] = ()


@dataclass
class SummaryExecutionCollector:
    """Collect runtime summary telemetry for later rendering/reporting."""

    results: list[SummaryResult] = field(default_factory=list)
    performance_rows: list[SummaryPerformanceRow] = field(default_factory=list)

    def record(self, result: SummaryResult) -> None:
        self.results.append(result)
        if result.elapsed_seconds is not None:
            self.performance_rows.append(
                SummaryPerformanceRow(
                    profile_name=result.profile_name,
                    provider=result.provider,
                    model=result.model,
                    newsletter_type=result.newsletter_type,
                    input_char_count=result.input_char_count,
                    output_char_count=result.output_char_count,
                    elapsed_seconds=result.elapsed_seconds,
                    status=result.status,
                    variant_name=result.variant_name,
                )
            )

    def build_report(self, plan: SummaryPlan) -> SummaryExecutionReport:
        profiles_used = sorted(
            {result.profile_name for result in self.results if result.profile_name}
        )
        models_used = sorted({result.model for result in self.results if result.model})
        routing: dict[tuple[str, str, str], int] = {}
        for result in self.results:
            key = (result.newsletter_type, result.profile_name, result.model)
            routing[key] = routing.get(key, 0) + 1
        return SummaryExecutionReport(
            mode=plan.mode,
            output_variants=plan.output_variants,
            selected_profiles=plan.selected_profiles,
            profiles_used=tuple(profiles_used),
            models_used=tuple(models_used),
            summaries_ollama=sum(
                1 for result in self.results if result.status == "ollama"
            ),
            summaries_cache=sum(
                1
                for result in self.results
                if result.status in {"cache", "legacy_cache"}
            ),
            summaries_fallback=sum(
                1 for result in self.results if result.status == "fallback"
            ),
            summaries_errors=sum(
                1 for result in self.results if result.status == "error"
            ),
            routing_counts=tuple(
                SummaryRoutingCount(
                    newsletter_type=newsletter_type,
                    profile_name=profile_name,
                    model=model,
                    count=count,
                )
                for (newsletter_type, profile_name, model), count in sorted(
                    routing.items()
                )
            ),
            performance_rows=tuple(self.performance_rows),
            anomaly_rows=tuple(self._anomaly_rows()),
        )

    def _anomaly_rows(self) -> list[SummaryAnomalyRow]:
        rows: list[SummaryAnomalyRow] = []
        for result in self.results:
            if result.status not in {"fallback", "error"}:
                if result.stop_reason is None or result.stop_reason in {
                    "done",
                    "provider_length",
                }:
                    continue
            rows.append(
                SummaryAnomalyRow(
                    subject=result.subject,
                    profile_name=result.profile_name,
                    status=result.status,
                    stop_reason=result.stop_reason,
                    output_chars=result.output_char_count,
                    elapsed_seconds=result.elapsed_seconds,
                    cached=result.cached,
                )
            )
        return rows


def _resolve_profile(
    profile_set: SummaryProfileSet, profile_name: str
) -> SummaryProfile:
    profile = profile_set.profiles.get(profile_name)
    if profile is None:
        raise UnknownSummaryProfileError(f"Unknown summary profile {profile_name!r}.")
    if not profile.enabled:
        raise DisabledSummaryProfileError(
            f"Summary profile {profile_name!r} is disabled."
        )
    return profile


def _resolve_routed_profile_name(
    profile_set: SummaryProfileSet, newsletter_type: str
) -> str:
    if newsletter_type in profile_set.type_routes:
        return profile_set.type_routes[newsletter_type]
    if "default" in profile_set.type_routes:
        return profile_set.type_routes["default"]
    if profile_set.fallback_profile:
        return profile_set.fallback_profile
    return profile_set.default_profile


def _build_job(
    entry: DigestEntry, profile_name: str, profile: SummaryProfile, variant_name: str
) -> SummaryJob:
    parsed = entry.classified.parsed
    return SummaryJob(
        message_key=parsed.message_key,
        content_hash=parsed.content_hash,
        canonical_newsletter_type=entry.classified.newsletter_type,
        summary_input_hash=parsed.content_hash,
        profile_name=profile_name,
        prompt_style=profile.prompt_style,
        provider=profile.provider,
        model=profile.model,
        options=dict(profile.options),
        temperature=profile.temperature,
        num_ctx=profile.num_ctx,
        timeout_seconds=profile.timeout_seconds,
        variant_name=variant_name,
    )


def resolve_summary_plan(
    entries: list[DigestEntry],
    profile_set: SummaryProfileSet,
    cli_options: SummaryCliOptions,
) -> SummaryPlan:
    """Resolve summary routing for already-classified digest entries."""
    if cli_options.summary_variants:
        jobs_by_variant: dict[str, tuple[SummaryJob, ...]] = {}
        for variant_name in cli_options.summary_variants:
            profile = _resolve_profile(profile_set, variant_name)
            jobs_by_variant[variant_name] = tuple(
                _build_job(entry, variant_name, profile, variant_name)
                for entry in entries
            )
        return SummaryPlan(
            mode="variants",
            selected_profiles=tuple(cli_options.summary_variants),
            type_routes_used={},
            jobs_by_variant=jobs_by_variant,
            output_variants=tuple(cli_options.summary_variants),
        )

    if cli_options.summary_profile:
        profile_name = cli_options.summary_profile
        profile = _resolve_profile(profile_set, profile_name)
        jobs = tuple(
            _build_job(entry, profile_name, profile, "default") for entry in entries
        )
        return SummaryPlan(
            mode="single_profile",
            selected_profiles=(profile_name,),
            type_routes_used={},
            jobs_by_variant={"default": jobs},
            output_variants=("default",),
        )

    if cli_options.summary_type_routing:
        route_jobs: list[SummaryJob] = []
        routes_used: dict[str, str] = {}
        for entry in entries:
            profile_name = _resolve_routed_profile_name(
                profile_set, entry.classified.newsletter_type
            )
            profile = _resolve_profile(profile_set, profile_name)
            routes_used[entry.classified.newsletter_type] = profile_name
            route_jobs.append(_build_job(entry, profile_name, profile, "default"))
        return SummaryPlan(
            mode="type_routed",
            selected_profiles=tuple(sorted(set(routes_used.values()))),
            type_routes_used=routes_used,
            jobs_by_variant={"default": tuple(route_jobs)},
            output_variants=("default",),
        )

    profile_name = profile_set.default_profile
    profile = _resolve_profile(profile_set, profile_name)
    jobs = tuple(
        _build_job(entry, profile_name, profile, "default") for entry in entries
    )
    return SummaryPlan(
        mode="single_profile",
        selected_profiles=(profile_name,),
        type_routes_used={},
        jobs_by_variant={"default": jobs},
        output_variants=("default",),
    )


def timed_result(
    *,
    start: float,
    message_key: str,
    subject: str = "",
    newsletter_type: str,
    profile_name: str,
    provider: str,
    model: str,
    prompt_style: str,
    status: SummaryResultStatus,
    summary_text: str | None,
    error_message: str | None,
    input_char_count: int,
    body_chars: int = 0,
    prompt_chars: int = 0,
    link_count: int = 0,
    stop_reason: StreamStopReason | None = None,
    cached: bool = False,
    variant_name: str,
) -> SummaryResult:
    """Create a SummaryResult with elapsed timing pre-populated."""
    output_char_count = len(summary_text or "")
    is_cached = cached or status in {"cache", "legacy_cache"}
    return SummaryResult(
        message_key=message_key,
        subject=subject,
        newsletter_type=newsletter_type,
        profile_name=profile_name,
        provider=provider,
        model=model,
        prompt_style=prompt_style,
        status=status,
        summary_text=summary_text,
        error_message=error_message,
        elapsed_seconds=perf_counter() - start,
        input_char_count=input_char_count,
        output_char_count=output_char_count,
        body_chars=body_chars,
        prompt_chars=prompt_chars,
        link_count=link_count,
        stop_reason=stop_reason,
        cached=is_cached,
        variant_name=variant_name,
    )
