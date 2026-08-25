"""Local summarisation helpers and summary plan execution."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse

from rollup.cache_keys import canonicalize_provider_options
from rollup.error_sanitize import sanitize_provider_message
from rollup.llm_client import (
    CompletionRequest,
    OllamaError,
    ProviderAvailabilityCache,
    check_ollama_available,
    is_local_ollama,
    validate_ollama_url,
)
from rollup.models import ClassifiedMessage, DigestEntry, SummarySource
from rollup.models import DigestSummaryAnomalyRow, DigestSummaryMetadata, DigestSummaryRouteStat
from rollup.ollama_stream import (
    StreamStopReason,
    is_stop_reason_cacheable,
)
from rollup.provider_errors import is_provider_call_error
from rollup.provider_options import (
    normalize_litellm_cache_options,
    normalize_ollama_cache_options,
)
from rollup.summary_plan import (
    SummaryExecutionCollector,
    SummaryJob,
    SummaryPlan,
    SummaryResultStatus,
    timed_result,
)
from rollup.summary_profiles import summary_job_options_for_cache

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PROMPT_VERSION = 3

MAX_OUTPUT_CHARS_BY_STYLE = {
    "rough": 1500,
    "standard": 3000,
    "deep": 6000,
    "preserve": 16000,
}

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
PROMPT_STYLE_INSTRUCTIONS = {
    "rough": (
        "Write 1-3 bullets. Start with the first bullet on line 1 — no intro sentence. "
        "Focus on what this is and the main takeaway."
    ),
    "standard": (
        "Write 2-5 bullets. Start with the first bullet on line 1 — no intro sentence. "
        "Cover key facts, implications, and useful links where available."
    ),
    "deep": (
        "Write a compact synthesis in bullets or short paragraphs. "
        "Start with substance on line 1 — no intro sentence. "
        "Preserve nuance, caveats, dates, and numbers. "
        "Distinguish news from opinion and explain why it matters."
    ),
    "preserve": (
        "This is an item list (jobs, papers, alerts, etc.). "
        "Output every distinct item as its own bullet. "
        "Light editing only: fix broken line wraps, remove duplicate headers/footers, "
        "and strip unsubscribe boilerplate. Do not merge, rank, or omit items. "
        "Do not add commentary. Start with the first item — no intro line."
    ),
}

_INTRO_LINE_RE = re.compile(
    r"^(?:"
    r"here(?:'s| is| are)\s+(?:a\s+)?(?:brief\s+)?(?:\d+[- ]?)?(?:bullet\s+)?"
    r"(?:summary|overview|digest|roundup)\b[^.!?]*[.:!]?\s*$|"
    r"below\s+is\s+(?:a\s+)?(?:brief\s+)?(?:summary|overview)\b[^.!?]*[.:!]?\s*$|"
    r"(?:summary|key\s+points?|main\s+points?|highlights?|takeaways?)\s*:?\s*$|"
    r"in\s+(?:this\s+)?(?:newsletter|email|message)\b[^.!?]*[.:!]?\s*$"
    r")",
    re.IGNORECASE,
)

_WORTH_TAIL_LINE_RE = re.compile(
    r"^(?:[-*]\s+)?(?:\*\*)?worth\s+(?:opening|reading|clicking)\??(?:\*\*)?\b",
    re.IGNORECASE,
)


def _strip_trailing_worth_sections(lines: list[str]) -> None:
    """Remove trailing recommendation tails such as 'Worth opening? Yes, if…'."""
    while lines:
        stripped = lines[-1].strip()
        if not stripped:
            lines.pop()
            continue
        if _WORTH_TAIL_LINE_RE.match(stripped):
            lines.pop()
            continue
        break


def clean_summary_output(text: str) -> str:
    """Drop leading meta lines and trailing worth recommendation sections."""
    lines = text.strip().splitlines()
    while lines:
        stripped = lines[0].strip()
        if not stripped:
            lines.pop(0)
            continue
        if _INTRO_LINE_RE.match(stripped):
            lines.pop(0)
            continue
        break
    _strip_trailing_worth_sections(lines)
    return "\n".join(lines).strip()


def max_output_chars_for_style(prompt_style: str) -> int:
    return MAX_OUTPUT_CHARS_BY_STYLE.get(
        prompt_style, MAX_OUTPUT_CHARS_BY_STYLE["standard"]
    )


def _has_extreme_repetition(text: str, *, window: int = 40, min_repeats: int = 5) -> bool:
    if len(text) < window * min_repeats:
        return False
    tail = text[-window * min_repeats :]
    segment = tail[-window:]
    return tail.count(segment) >= min_repeats


def finalize_summary_output(text: str, *, prompt_style: str) -> str:
    cleaned = clean_summary_output(text)
    cap = max_output_chars_for_style(prompt_style)
    if len(cleaned) > cap:
        return cleaned[: cap - 1] + "…"
    return cleaned


def is_summary_usable(
    text: str, *, prompt_style: str, stop_reason: StreamStopReason
) -> bool:
    if not is_stop_reason_cacheable(stop_reason):
        return False
    cleaned = clean_summary_output(text)
    if not cleaned.strip():
        return False
    if len(cleaned) > max_output_chars_for_style(prompt_style):
        return False
    if _has_extreme_repetition(cleaned):
        return False
    return True


def _link_count(parsed) -> int:
    structured = len(getattr(parsed, "link_items", ()) or ())
    return max(structured, len(parsed.links), parsed.html_link_count)


@dataclass(frozen=True)
class SummarizeMessageResult:
    text: str
    stop_reason: StreamStopReason
    output_chars: int
    elapsed_seconds: float
    body_chars: int
    prompt_chars: int
    link_count: int


def _load_prompt(newsletter_type: str, *, prompt_style: str = "standard") -> str:
    common_name = (
        "_common_preserve.txt" if prompt_style == "preserve" else "_common.txt"
    )
    common_path = PROMPTS_DIR / common_name
    type_path = PROMPTS_DIR / f"{newsletter_type}.txt"
    parts: list[str] = []
    if common_path.exists():
        parts.append(common_path.read_text(encoding="utf-8").strip())
    if type_path.exists():
        parts.append(type_path.read_text(encoding="utf-8").strip())
    return "\n\n".join(parts)


def build_prompt(
    classified: ClassifiedMessage, body_excerpt: str, prompt_style: str = "standard"
) -> str:
    p = classified.parsed
    template = _load_prompt(classified.newsletter_type, prompt_style=prompt_style)
    style_instructions = PROMPT_STYLE_INSTRUCTIONS.get(
        prompt_style, PROMPT_STYLE_INSTRUCTIONS["standard"]
    )
    return "{style}\n\n{template}".format(
        style=style_instructions, template=template
    ).format(
        subject=p.subject,
        sender=p.sender,
        newsletter_type=classified.newsletter_type,
        body_excerpt=body_excerpt,
    )


def build_ollama_generate_payload(
    *,
    model: str,
    prompt: str,
    stream: bool,
    options: dict[str, object] | None,
    temperature: float,
    num_ctx: int | None,
    think: bool | str,
) -> dict[str, object]:
    """Build an Ollama /api/generate request body."""
    payload_options: dict[str, object] = dict(options or {})
    payload_options.setdefault("temperature", temperature)
    if num_ctx is not None:
        payload_options.setdefault("num_ctx", num_ctx)
    return {
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "options": payload_options,
        "think": think,
    }


def summarize_message(
    classified: ClassifiedMessage,
    ollama_url: str,
    model: str,
    max_chars: int,
    timeout: int = 120,
    *,
    prompt_style: str = "standard",
    options: dict[str, object] | None = None,
    temperature: float = 0.2,
    num_ctx: int | None = None,
    think: bool | str = False,
    quiet: bool = False,
    allow_remote: bool = False,
) -> SummarizeMessageResult:
    from rollup.llm_client import OllamaClient

    parsed = classified.parsed
    excerpt = parsed.body_text[:max_chars]
    prompt = build_prompt(classified, excerpt, prompt_style=prompt_style)
    body_chars = len(excerpt)
    prompt_chars = len(prompt)
    link_count = _link_count(parsed)
    max_output_chars = max_output_chars_for_style(prompt_style)
    num_predict = None
    if options and "num_predict" in options:
        num_predict = int(options["num_predict"])  # type: ignore[arg-type]
    client = OllamaClient(ollama_url=ollama_url, allow_remote=allow_remote)
    stream_result = client.complete(
        CompletionRequest(
            model=model,
            prompt=prompt,
            stream=not quiet,
            timeout_seconds=float(timeout),
            temperature=temperature,
            max_tokens=num_predict,
            num_ctx=num_ctx,
            think=think,
            options=options,
        ),
        max_output_chars=max_output_chars,
        max_wall_seconds=float(timeout),
        show_progress=not quiet,
    )
    text = finalize_summary_output(stream_result.text, prompt_style=prompt_style)
    return SummarizeMessageResult(
        text=text,
        stop_reason=stream_result.stop_reason,
        output_chars=len(text),
        elapsed_seconds=stream_result.elapsed_seconds,
        body_chars=body_chars,
        prompt_chars=prompt_chars,
        link_count=link_count,
    )


def compute_summary_input_hash(
    classified: ClassifiedMessage,
    *,
    prompt_style: str,
    max_chars: int,
) -> str:
    """Hash the exact LLM input: excerpt, rendered prompt, and max_chars."""
    excerpt = classified.parsed.body_text[:max_chars]
    prompt = build_prompt(classified, excerpt, prompt_style=prompt_style)
    payload = json.dumps(
        {"excerpt": excerpt, "max_chars": max_chars, "prompt": prompt},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def legacy_cache_compatible(job: SummaryJob) -> bool:
    """Legacy summaries only satisfy standard/default-compatible requests."""
    return job.prompt_style == "standard" and job.profile_name == "standard"


class OllamaAvailabilityCache:
    """Backward-compatible Ollama-only availability cache."""

    def __init__(self, base_url: str, *, allow_remote: bool = False) -> None:
        self._cache = ProviderAvailabilityCache(
            ollama_url=base_url,
            allow_remote=allow_remote,
            llm_api_base=None,
        )

    def check(self, model: str) -> tuple[bool, str]:
        return self._cache.check("ollama", model)


def _fresh_result_status(provider: str) -> SummaryResultStatus:
    return "litellm" if provider == "litellm" else "ollama"


def _fresh_summary_source(provider: str) -> SummarySource:
    return "litellm" if provider == "litellm" else "ollama"


def _cache_options_for_job(
    job: SummaryJob,
    *,
    llm_api_base: str | None,
) -> dict[str, object]:
    if job.provider == "litellm":
        max_tokens = None
        if job.options and "num_predict" in job.options:
            max_tokens = int(job.options["num_predict"])  # type: ignore[arg-type]
        return normalize_litellm_cache_options(
            temperature=job.temperature,
            max_tokens=max_tokens,
            timeout_seconds=job.timeout_seconds,
            api_base=llm_api_base,
        )
    return summary_job_options_for_cache(job.options, think=job.think)


def _max_tokens_from_job(job: SummaryJob) -> int | None:
    if job.options and "num_predict" in job.options:
        return int(job.options["num_predict"])  # type: ignore[arg-type]
    return None


def _complete_summary_job(
    classified: ClassifiedMessage,
    job: SummaryJob,
    *,
    availability: ProviderAvailabilityCache,
    max_chars: int,
    quiet: bool,
) -> SummarizeMessageResult:
    parsed = classified.parsed
    excerpt = parsed.body_text[:max_chars]
    prompt = build_prompt(classified, excerpt, prompt_style=job.prompt_style)
    body_chars = len(excerpt)
    prompt_chars = len(prompt)
    link_count = _link_count(parsed)
    max_output_chars = max_output_chars_for_style(job.prompt_style)
    client = availability.get_client(job.provider)
    stream_result = client.complete(
        CompletionRequest(
            model=job.model,
            prompt=prompt,
            stream=not quiet,
            timeout_seconds=float(job.timeout_seconds or 120),
            temperature=job.temperature,
            max_tokens=_max_tokens_from_job(job),
            num_ctx=job.num_ctx if job.provider == "ollama" else None,
            think=job.think if job.provider == "ollama" else False,
            options=job.options if job.provider == "ollama" else None,
        ),
        max_output_chars=max_output_chars,
        max_wall_seconds=float(job.timeout_seconds or 120),
        show_progress=not quiet,
    )
    text = finalize_summary_output(stream_result.text, prompt_style=job.prompt_style)
    return SummarizeMessageResult(
        text=text,
        stop_reason=stream_result.stop_reason,
        output_chars=len(text),
        elapsed_seconds=stream_result.elapsed_seconds,
        body_chars=body_chars,
        prompt_chars=prompt_chars,
        link_count=link_count,
    )


def build_summary_cache_key_parts(
    *,
    message_key: str,
    content_hash: str,
    newsletter_type: str,
    provider: str,
    profile_name: str,
    model: str,
    prompt_style: str,
    prompt_version: int,
    temperature: float,
    num_ctx: int | None,
    options: dict[str, object] | None,
    summary_input_hash: str,
) -> tuple[object, ...]:
    options_json = canonicalize_provider_options(options)
    return (
        message_key,
        content_hash,
        newsletter_type,
        provider,
        profile_name,
        model,
        prompt_style,
        prompt_version,
        temperature,
        num_ctx,
        options_json,
        summary_input_hash,
    )


def apply_summaries(
    entries: list[DigestEntry],
    ollama_url: str,
    model: str,
    max_chars: int,
    allow_remote: bool,
    conn=None,
    rebuild: bool = False,
    *,
    quiet: bool = False,
) -> list[DigestEntry]:
    """Apply Ollama summaries to digest entries with cache support."""
    validate_ollama_url(ollama_url, allow_remote)
    target = "local" if is_local_ollama(ollama_url) else "remote"
    logger.info("Ollama summarisation target: %s", target)

    ok, msg = check_ollama_available(ollama_url, model)
    if not ok:
        logger.warning("Ollama unavailable: %s — using preview fallback", msg)
        return [_fallback_entry(e) for e in entries]

    result: list[DigestEntry] = []
    total = len(entries)
    for index, entry in enumerate(entries, start=1):
        classified = entry.classified
        parsed = classified.parsed
        if not parsed.body_text.strip():
            result.append(
                DigestEntry(classified=classified, summary=None, summary_source="none")
            )
            continue
        if conn and not rebuild:
            cached = None
            try:
                from rollup.state import get_cached_summary

                cached = get_cached_summary(
                    conn,
                    parsed.message_key,
                    parsed.content_hash,
                    model,
                    classified.newsletter_type,
                )
            except Exception as exc:
                logger.warning("Cache read failed for %s: %s", parsed.subject, exc)
            if cached:
                logger.debug(
                    "Ollama [%d/%d] cache hit: %r", index, total, parsed.subject
                )
                result.append(
                    DigestEntry(
                        classified=classified, summary=cached, summary_source="cache"
                    )
                )
                continue
        logger.info(
            "Ollama [%d/%d] summarising: %r (model=%s)",
            index,
            total,
            parsed.subject,
            model,
        )
        try:
            generation = summarize_message(
                classified, ollama_url, model, max_chars, quiet=quiet
            )
        except Exception as exc:
            if not is_provider_call_error(exc):
                raise
            logger.warning("Summary failed for %s: %s", parsed.subject, exc)
            result.append(_fallback_entry(entry))
            continue
        if not is_summary_usable(
            generation.text,
            prompt_style="standard",
            stop_reason=generation.stop_reason,
        ):
            logger.warning(
                "Summary unusable for %s: stop_reason=%s output_chars=%d",
                parsed.subject,
                generation.stop_reason,
                generation.output_chars,
            )
            result.append(_fallback_entry(entry))
            continue
        summary = generation.text
        if conn:
            try:
                from rollup.state import store_summary

                store_summary(
                    conn,
                    parsed.message_key,
                    parsed.content_hash,
                    classified.newsletter_type,
                    model,
                    summary,
                    __import__("datetime").datetime.now().astimezone(),
                )
            except Exception as exc:
                logger.warning(
                    "Failed to cache summary for %s: %s", parsed.subject, exc
                )
        result.append(
            DigestEntry(classified=classified, summary=summary, summary_source="ollama")
        )
    return result


class SummaryExecutionOutput:
    def __init__(
        self,
        entries_by_variant: dict[str, list[DigestEntry]],
        summary_metadata_by_variant: dict[str, DigestSummaryMetadata],
    ):
        self.entries_by_variant = entries_by_variant
        self.summary_metadata_by_variant = summary_metadata_by_variant


def execute_summary_plan(
    *,
    entries: list[DigestEntry],
    plan: SummaryPlan,
    ollama_url: str,
    default_model: str,
    max_chars: int,
    allow_remote: bool,
    conn=None,
    rebuild: bool = False,
    quiet: bool = False,
    llm_api_base: str | None = None,
) -> SummaryExecutionOutput:
    """Execute a summary plan without re-running parse/classify/filter."""
    validate_ollama_url(ollama_url, allow_remote)
    target = "local" if is_local_ollama(ollama_url) else "remote"
    logger.info("LLM summarisation target (Ollama transport): %s", target)

    entries_by_key = {entry.classified.parsed.message_key: entry for entry in entries}
    rendered_by_variant: dict[str, list[DigestEntry]] = {}
    metadata_by_variant: dict[str, DigestSummaryMetadata] = {}
    availability = ProviderAvailabilityCache(
        ollama_url=ollama_url,
        allow_remote=allow_remote,
        llm_api_base=llm_api_base,
    )
    for variant_name in plan.output_variants:
        jobs = list(plan.jobs_by_variant.get(variant_name, ()))
        collector = SummaryExecutionCollector()
        rendered_entries: list[DigestEntry] = []
        total_jobs = len(jobs)
        if total_jobs:
            logger.info(
                "Ollama: processing %d summary jobs (variant=%s)",
                total_jobs,
                variant_name,
            )
        for job_index, job in enumerate(jobs, start=1):
            entry = entries_by_key[job.message_key]
            classified = entry.classified
            parsed = classified.parsed
            if not parsed.body_text.strip():
                rendered_entries.append(
                    DigestEntry(
                        classified=classified, summary=None, summary_source="none"
                    )
                )
                collector.record(
                    timed_result(
                        start=perf_counter(),
                        message_key=job.message_key,
                        subject=parsed.subject,
                        newsletter_type=job.canonical_newsletter_type,
                        profile_name=job.profile_name,
                        provider=job.provider,
                        model=job.model or default_model,
                        prompt_style=job.prompt_style,
                        status="skipped",
                        summary_text=None,
                        error_message=None,
                        input_char_count=0,
                        variant_name=variant_name,
                    )
                )
                continue
            start = perf_counter()
            input_excerpt = parsed.body_text[:max_chars]
            input_char_count = len(input_excerpt)
            body_chars = input_char_count
            prompt_chars = len(
                build_prompt(classified, input_excerpt, prompt_style=job.prompt_style)
            )
            link_count = _link_count(parsed)
            model = job.model or default_model
            summary_input_hash = compute_summary_input_hash(
                classified,
                prompt_style=job.prompt_style,
                max_chars=max_chars,
            )
            if conn and not rebuild:
                try:
                    from rollup.state import (
                        get_cached_summary,
                        get_cached_summary_generation,
                    )

                    cache_options = _cache_options_for_job(
                        job, llm_api_base=llm_api_base
                    )
                    cached = get_cached_summary_generation(
                        conn,
                        message_key=parsed.message_key,
                        content_hash=parsed.content_hash,
                        newsletter_type=classified.newsletter_type,
                        provider=job.provider,
                        profile_name=job.profile_name,
                        model=model,
                        prompt_style=job.prompt_style,
                        prompt_version=PROMPT_VERSION,
                        temperature=job.temperature,
                        num_ctx=job.num_ctx,
                        options=cache_options,
                        summary_input_hash=summary_input_hash,
                    )
                    if cached:
                        logger.debug(
                            "Ollama [%d/%d] cache hit: %r",
                            job_index,
                            total_jobs,
                            parsed.subject,
                        )
                        rendered_entries.append(
                            DigestEntry(
                                classified=classified,
                                summary=cached,
                                summary_source="cache",
                            )
                        )
                        collector.record(
                            timed_result(
                                start=start,
                                message_key=job.message_key,
                                subject=parsed.subject,
                                newsletter_type=job.canonical_newsletter_type,
                                profile_name=job.profile_name,
                                provider=job.provider,
                                model=model,
                                prompt_style=job.prompt_style,
                                status="cache",
                                summary_text=cached,
                                error_message=None,
                                input_char_count=input_char_count,
                                body_chars=body_chars,
                                prompt_chars=prompt_chars,
                                link_count=link_count,
                                stop_reason="done",
                                cached=True,
                                variant_name=variant_name,
                            )
                        )
                        continue
                    if legacy_cache_compatible(job):
                        legacy = get_cached_summary(
                            conn,
                            parsed.message_key,
                            parsed.content_hash,
                            model,
                            classified.newsletter_type,
                        )
                        if legacy:
                            logger.debug(
                                "Ollama [%d/%d] legacy cache hit: %r",
                                job_index,
                                total_jobs,
                                parsed.subject,
                            )
                            rendered_entries.append(
                                DigestEntry(
                                    classified=classified,
                                    summary=legacy,
                                    summary_source="cache",
                                )
                            )
                            collector.record(
                                timed_result(
                                    start=start,
                                    message_key=job.message_key,
                                    subject=parsed.subject,
                                    newsletter_type=job.canonical_newsletter_type,
                                    profile_name=job.profile_name,
                                    provider=job.provider,
                                    model=model,
                                    prompt_style=job.prompt_style,
                                    status="legacy_cache",
                                    summary_text=legacy,
                                    error_message=None,
                                    input_char_count=input_char_count,
                                    body_chars=body_chars,
                                    prompt_chars=prompt_chars,
                                    link_count=link_count,
                                    stop_reason="done",
                                    cached=True,
                                    variant_name=variant_name,
                                )
                            )
                            continue
                except Exception as exc:
                    logger.warning("Cache read failed for %s: %s", parsed.subject, exc)
            ok, msg = availability.check(job.provider, model)
            if not ok:
                hint = (
                    f"Try: ollama pull {model}"
                    if job.provider == "ollama"
                    else "check LiteLLM credentials and model string"
                )
                error_message = (
                    f"Model unavailable for profile {job.profile_name!r} "
                    f"({job.provider}): {msg}. {hint}"
                )
                logger.warning("%s", error_message)
                fallback = _fallback_entry(entry)
                rendered_entries.append(fallback)
                collector.record(
                    timed_result(
                        start=start,
                        message_key=job.message_key,
                        subject=parsed.subject,
                        newsletter_type=job.canonical_newsletter_type,
                        profile_name=job.profile_name,
                        provider=job.provider,
                        model=model,
                        prompt_style=job.prompt_style,
                        status="error",
                        summary_text=fallback.summary,
                        error_message=error_message,
                        input_char_count=input_char_count,
                        body_chars=body_chars,
                        prompt_chars=prompt_chars,
                        link_count=link_count,
                        stop_reason="http_error",
                        variant_name=variant_name,
                    )
                )
                continue
            logger.info(
                "LLM [%d/%d] summarising: %r (provider=%s model=%s, profile=%s, "
                "body_chars=%d, prompt_chars=%d, link_count=%d)",
                job_index,
                total_jobs,
                parsed.subject,
                job.provider,
                model,
                job.profile_name,
                body_chars,
                prompt_chars,
                link_count,
            )
            try:
                generation = _complete_summary_job(
                    classified,
                    SummaryJob(
                        message_key=job.message_key,
                        content_hash=job.content_hash,
                        canonical_newsletter_type=job.canonical_newsletter_type,
                        summary_input_hash=job.summary_input_hash,
                        profile_name=job.profile_name,
                        prompt_style=job.prompt_style,
                        provider=job.provider,
                        model=model,
                        options=job.options,
                        think=job.think,
                        temperature=job.temperature,
                        num_ctx=job.num_ctx,
                        timeout_seconds=job.timeout_seconds,
                        variant_name=job.variant_name,
                    ),
                    availability=availability,
                    max_chars=max_chars,
                    quiet=quiet,
                )
            except Exception as exc:
                if not is_provider_call_error(exc):
                    raise
                logger.warning(
                    "Summary failed for %s: %s",
                    parsed.subject,
                    sanitize_provider_message(str(exc)),
                )
                fallback = _fallback_entry(entry)
                rendered_entries.append(fallback)
                collector.record(
                    timed_result(
                        start=start,
                        message_key=job.message_key,
                        subject=parsed.subject,
                        newsletter_type=job.canonical_newsletter_type,
                        profile_name=job.profile_name,
                        provider=job.provider,
                        model=model,
                        prompt_style=job.prompt_style,
                        status="fallback",
                        summary_text=fallback.summary,
                        error_message=str(exc),
                        input_char_count=input_char_count,
                        body_chars=body_chars,
                        prompt_chars=prompt_chars,
                        link_count=link_count,
                        stop_reason="http_error",
                        variant_name=variant_name,
                    )
                )
                continue
            if not is_summary_usable(
                generation.text,
                prompt_style=job.prompt_style,
                stop_reason=generation.stop_reason,
            ):
                logger.warning(
                    "Summary unusable for %r (profile=%s): stop_reason=%s "
                    "output_chars=%d",
                    parsed.subject,
                    job.profile_name,
                    generation.stop_reason,
                    generation.output_chars,
                )
                fallback = _fallback_entry(entry)
                rendered_entries.append(fallback)
                collector.record(
                    timed_result(
                        start=start,
                        message_key=job.message_key,
                        subject=parsed.subject,
                        newsletter_type=job.canonical_newsletter_type,
                        profile_name=job.profile_name,
                        provider=job.provider,
                        model=model,
                        prompt_style=job.prompt_style,
                        status="fallback",
                        summary_text=fallback.summary,
                        error_message=(
                            f"unusable summary (stop_reason={generation.stop_reason})"
                        ),
                        input_char_count=input_char_count,
                        body_chars=body_chars,
                        prompt_chars=prompt_chars,
                        link_count=link_count,
                        stop_reason=generation.stop_reason,
                        variant_name=variant_name,
                    )
                )
                continue
            summary = generation.text
            if conn:
                try:
                    from rollup.state import store_summary_generation
                    import datetime as _dt

                    store_summary_generation(
                        conn,
                        message_key=parsed.message_key,
                        content_hash=parsed.content_hash,
                        newsletter_type=classified.newsletter_type,
                        provider=job.provider,
                        profile_name=job.profile_name,
                        model=model,
                        prompt_style=job.prompt_style,
                        prompt_version=PROMPT_VERSION,
                        temperature=job.temperature,
                        num_ctx=job.num_ctx,
                        options=_cache_options_for_job(
                            job, llm_api_base=llm_api_base
                        ),
                        summary_input_hash=summary_input_hash,
                        summary=summary,
                        created_at=_dt.datetime.now().astimezone(),
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to cache summary for %s: %s", parsed.subject, exc
                    )
            fresh_status = _fresh_result_status(job.provider)
            fresh_source = _fresh_summary_source(job.provider)
            rendered_entries.append(
                DigestEntry(
                    classified=classified,
                    summary=summary,
                    summary_source=fresh_source,
                )
            )
            collector.record(
                timed_result(
                    start=start,
                    message_key=job.message_key,
                    subject=parsed.subject,
                    newsletter_type=job.canonical_newsletter_type,
                    profile_name=job.profile_name,
                    provider=job.provider,
                    model=model,
                    prompt_style=job.prompt_style,
                    status=fresh_status,
                    summary_text=summary,
                    error_message=None,
                    input_char_count=input_char_count,
                    body_chars=body_chars,
                    prompt_chars=prompt_chars,
                    link_count=link_count,
                    stop_reason=generation.stop_reason,
                    variant_name=variant_name,
                )
            )
        rendered_by_variant[variant_name] = rendered_entries
        report = collector.build_report(plan)
        metadata_by_variant[variant_name] = DigestSummaryMetadata(
            mode=report.mode,
            profiles_used=report.profiles_used,
            models_used=report.models_used,
            summaries_ollama=report.summaries_ollama,
            summaries_litellm=report.summaries_litellm,
            summaries_cache=report.summaries_cache,
            summaries_fallback=report.summaries_fallback,
            summaries_errors=report.summaries_errors,
            selected_profiles=report.selected_profiles,
            output_variants=report.output_variants,
            routing_counts=tuple(
                DigestSummaryRouteStat(
                    newsletter_type=row.newsletter_type,
                    profile_name=row.profile_name,
                    provider=row.provider,
                    model=row.model,
                    count=row.count,
                )
                for row in report.routing_counts
            ),
            anomaly_rows=tuple(
                DigestSummaryAnomalyRow(
                    subject=row.subject,
                    profile_name=row.profile_name,
                    status=row.status,
                    stop_reason=row.stop_reason,
                    output_chars=row.output_chars,
                    elapsed_seconds=row.elapsed_seconds,
                    cached=row.cached,
                )
                for row in report.anomaly_rows
            ),
            variant_name=None if variant_name == "default" else variant_name,
        )
    return SummaryExecutionOutput(rendered_by_variant, metadata_by_variant)


def _fallback_entry(entry: DigestEntry) -> DigestEntry:
    parsed = entry.classified.parsed
    if parsed.preview:
        return DigestEntry(
            classified=entry.classified,
            summary=parsed.preview,
            summary_source="preview_fallback",
        )
    return DigestEntry(classified=entry.classified, summary=None, summary_source="none")
