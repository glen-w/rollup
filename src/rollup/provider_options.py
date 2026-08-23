"""Canonical provider option normalisation for cache identity and transport."""

from __future__ import annotations

from typing import Any

from rollup.cache_keys import canonicalize_provider_options

OLLAMA_ONLY_PROFILE_KEYS = frozenset({"think", "num_ctx"})


class ProviderOptionsError(ValueError):
    """Raised when profile options are invalid for a provider."""


def canonicalize_api_base(api_base: str | None) -> str:
    """Non-secret endpoint identity for cache keys."""
    if not api_base:
        return ""
    from urllib.parse import urlparse

    parsed = urlparse(api_base.strip())
    if not parsed.scheme or not parsed.netloc:
        return api_base.strip().rstrip("/")
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/") if parsed.path else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}{path}"


def validate_litellm_profile_options(
    profile_name: str,
    *,
    think: Any,
    num_ctx: int | None,
    options: dict[str, Any],
) -> None:
    """Reject LiteLLM profiles that carry Ollama-only generation knobs."""
    problems: list[str] = []
    if think not in (False, None):
        problems.append("think")
    if num_ctx is not None:
        problems.append("num_ctx")
    if options:
        problems.extend(sorted(options.keys()))
    if problems:
        joined = ", ".join(problems)
        raise ProviderOptionsError(
            f"LiteLLM profile {profile_name!r} cannot set Ollama-only options: {joined}"
        )


def reject_litellm_ollama_model(model: str, *, context: str) -> None:
    """Reject model strings that route native Ollama through LiteLLM."""
    lowered = model.strip().lower()
    if lowered.startswith("ollama/") or lowered.startswith("ollama_chat/"):
        raise ProviderOptionsError(
            f"{context}: model {model!r} routes Ollama through LiteLLM; "
            "use provider 'ollama' with --ollama-model instead."
        )


def normalize_ollama_cache_options(
    options: dict[str, object] | None,
    *,
    think: object,
    temperature: float,
    num_ctx: int | None,
    num_predict: int | None = None,
) -> dict[str, object]:
    out: dict[str, object] = dict(options or {})
    out.setdefault("temperature", temperature)
    if num_ctx is not None:
        out.setdefault("num_ctx", num_ctx)
    if num_predict is not None:
        out.setdefault("num_predict", num_predict)
    if think is not False:
        out["__rollup_think__"] = think
    return out


def normalize_litellm_cache_options(
    *,
    temperature: float,
    max_tokens: int | None,
    timeout_seconds: int | None,
    api_base: str | None,
) -> dict[str, object]:
    out: dict[str, object] = {"temperature": temperature}
    if max_tokens is not None:
        out["max_tokens"] = max_tokens
    if timeout_seconds is not None:
        out["timeout_seconds"] = timeout_seconds
    base = canonicalize_api_base(api_base)
    if base:
        out["api_base"] = base
    return out


def cache_options_json(options: dict[str, object] | None) -> str:
    return canonicalize_provider_options(options)
