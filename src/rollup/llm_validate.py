"""Planning-time validation for executable LLM jobs."""

from __future__ import annotations

from dataclasses import dataclass

from rollup.config import Config
from rollup.final_review_profiles import (
    FINAL_REVIEW_PROVIDERS,
    resolve_final_review_profile,
)
from rollup.llm_client import LlmExtraMissingError, validate_llm_api_base
from rollup.provider_options import (
    ProviderOptionsError,
    reject_litellm_ollama_model,
    validate_litellm_profile_options,
)
from rollup.summary_plan import SummaryJob, SummaryPlan
from rollup.summary_profiles import SUMMARY_PROVIDERS


class LlmJobValidationError(ValueError):
    """Raised when an executable LLM job cannot be resolved."""


@dataclass(frozen=True)
class ResolvedLlmJob:
    provider: str
    model: str
    kind: str


def _require_litellm_extra() -> None:
    try:
        import litellm  # noqa: F401
    except ImportError as exc:
        raise LlmExtraMissingError(
            "LiteLLM is not installed; pip install 'rollup[llm]'"
        ) from exc


def _resolve_global_model(config: Config) -> str | None:
    if config.llm_provider == "litellm":
        return config.llm_model or None
    return config.ollama_model


def resolve_fallback_model(config: Config) -> str:
    model = _resolve_global_model(config)
    if not model:
        raise LlmJobValidationError(
            "No model resolved for fallback/group-summary path; "
            "set --llm-model (LiteLLM) or --ollama-model / effort defaults (Ollama)."
        )
    if config.llm_provider == "litellm":
        reject_litellm_ollama_model(model, context="fallback model")
    return model


def validate_summary_job(job: SummaryJob, config: Config) -> ResolvedLlmJob:
    if job.provider not in SUMMARY_PROVIDERS:
        raise LlmJobValidationError(f"Unsupported summary provider {job.provider!r}")
    model = job.model
    if not model:
        model = resolve_fallback_model(config)
    if job.provider == "litellm":
        _require_litellm_extra()
        reject_litellm_ollama_model(model, context=f"profile {job.profile_name!r}")
        validate_litellm_profile_options(
            job.profile_name,
            think=job.think,
            num_ctx=job.num_ctx,
            options=dict(job.options or {}),
        )
    return ResolvedLlmJob(provider=job.provider, model=model, kind="summary")


def validate_executable_llm_jobs(config: Config, plan: SummaryPlan | None) -> list[ResolvedLlmJob]:
    """Validate every executable LLM job before network execution."""
    if config.no_ollama:
        return []

    validate_llm_api_base(config.llm_api_base)
    resolved: list[ResolvedLlmJob] = []
    seen: set[tuple[str, str, str]] = set()

    if plan is not None:
        for jobs in plan.jobs_by_variant.values():
            for job in jobs:
                item = validate_summary_job(job, config)
                key = (item.kind, item.provider, item.model)
                if key not in seen:
                    seen.add(key)
                    resolved.append(item)

    if config.group_summaries_enabled:
        provider = config.llm_provider
        model = resolve_fallback_model(config)
        if provider == "litellm":
            _require_litellm_extra()
            reject_litellm_ollama_model(model, context="group summary")
        key = ("group", provider, model)
        if key not in seen:
            seen.add(key)
            resolved.append(ResolvedLlmJob(provider=provider, model=model, kind="group"))

    if config.final_review_enabled:
        provider = config.final_review_provider
        if provider not in FINAL_REVIEW_PROVIDERS:
            raise LlmJobValidationError(
                f"Unsupported final review provider {provider!r}"
            )
        profile = resolve_final_review_profile(
            config.final_review_profile,
            model_override=config.final_review_model,
        )
        model = profile.model
        if not model:
            raise LlmJobValidationError("Final review model is required")
        if provider == "litellm":
            _require_litellm_extra()
            reject_litellm_ollama_model(model, context="final review")
        key = ("final_review", provider, model)
        if key not in seen:
            seen.add(key)
            resolved.append(
                ResolvedLlmJob(provider=provider, model=model, kind="final_review")
            )

    return resolved


def collect_reachable_transports(jobs: list[ResolvedLlmJob]) -> frozenset[str]:
    return frozenset(job.provider for job in jobs)
