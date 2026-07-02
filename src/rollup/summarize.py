"""Local summarisation helpers and summary plan execution."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse

from rollup.cache_keys import canonicalize_provider_options
from rollup.models import ClassifiedMessage, DigestEntry
from rollup.summary_plan import (
    SummaryExecutionCollector,
    SummaryJob,
    SummaryPlan,
    timed_result,
)
from rollup.models import DigestSummaryMetadata, DigestSummaryRouteStat

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
PROMPT_VERSION = 2

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
PROMPT_STYLE_INSTRUCTIONS = {
    "rough": (
        "Write 1-3 bullets. Start with the first bullet on line 1 — no intro sentence. "
        "Focus on what this is and whether it is worth clicking."
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


def clean_summary_output(text: str) -> str:
    """Drop leading meta lines such as 'Here's a summary in 2 bullets:'."""
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
    return "\n".join(lines).strip()


class OllamaError(Exception):
    pass


def validate_ollama_url(url: str, allow_remote: bool) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise OllamaError(
            f"Ollama URL scheme {parsed.scheme!r} is not supported; use http or https."
        )
    host = parsed.hostname
    if not host:
        raise OllamaError("Ollama URL must include a hostname.")
    if host not in LOCAL_HOSTS and not allow_remote:
        raise OllamaError(
            f"Ollama URL host {host!r} is not local. "
            "Pass --allow-remote-ollama to permit non-loopback endpoints."
        )


def is_local_ollama(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in LOCAL_HOSTS


def _load_prompt(newsletter_type: str) -> str:
    common_path = PROMPTS_DIR / "_common.txt"
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
    template = _load_prompt(classified.newsletter_type)
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


def _ollama_model_matches(requested: str, available: str) -> bool:
    if not requested or not available:
        return False
    if available == requested:
        return True
    if ":" not in requested and available.startswith(f"{requested}:"):
        return True
    return False


def check_ollama_available(base_url: str, model: str) -> tuple[bool, str]:
    """Check Ollama tags endpoint. Returns (ok, message)."""
    try:
        import requests
    except ImportError:
        return False, "requests not installed; pip install -e '.[ollama]'"

    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False, "Ollama URL must use http/https with a hostname."
    tags_url = f"{parsed.scheme}://{parsed.netloc}/api/tags"
    try:
        resp = requests.get(tags_url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        models = [m.get("name", "") for m in data.get("models", [])]
        if not any(_ollama_model_matches(model, m) for m in models):
            return (
                False,
                f"Model {model!r} not found in Ollama. Available: {models[:5]}",
            )
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def _consume_ollama_stream(resp, *, show_progress: bool) -> str:
    parts: list[str] = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        data = json.loads(line)
        chunk = data.get("response", "")
        if chunk:
            parts.append(chunk)
            if show_progress:
                total = sum(len(part) for part in parts)
                sys.stderr.write(f"\r  generating… {total} chars")
                sys.stderr.flush()
        if data.get("done"):
            if show_progress:
                eval_count = data.get("eval_count")
                suffix = f", {eval_count} tokens" if eval_count else ""
                sys.stderr.write(f"\r  generated{suffix}\n")
                sys.stderr.flush()
            break
    return clean_summary_output("".join(parts))


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
    quiet: bool = False,
) -> str:
    import requests

    excerpt = classified.parsed.body_text[:max_chars]
    prompt = build_prompt(classified, excerpt, prompt_style=prompt_style)
    payload_options = dict(options or {})
    payload_options.setdefault("temperature", temperature)
    if num_ctx is not None:
        payload_options.setdefault("num_ctx", num_ctx)
    use_stream = not quiet
    resp = requests.post(
        ollama_url,
        json={
            "model": model,
            "prompt": prompt,
            "stream": use_stream,
            "options": payload_options,
        },
        timeout=timeout,
        stream=use_stream,
    )
    resp.raise_for_status()
    if use_stream:
        return _consume_ollama_stream(resp, show_progress=True)
    data = resp.json()
    return clean_summary_output(data.get("response", ""))


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
    """Cache Ollama /api/tags availability checks for one execution."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self._results: dict[str, tuple[bool, str]] = {}

    def check(self, model: str) -> tuple[bool, str]:
        if model not in self._results:
            self._results[model] = check_ollama_available(self.base_url, model)
        return self._results[model]


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
            summary = summarize_message(
                classified, ollama_url, model, max_chars, quiet=quiet
            )
        except Exception as exc:
            logger.warning("Summary failed for %s: %s", parsed.subject, exc)
            result.append(_fallback_entry(entry))
            continue
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
) -> SummaryExecutionOutput:
    """Execute a summary plan without re-running parse/classify/filter."""
    validate_ollama_url(ollama_url, allow_remote)
    target = "local" if is_local_ollama(ollama_url) else "remote"
    logger.info("Ollama summarisation target: %s", target)

    entries_by_key = {entry.classified.parsed.message_key: entry for entry in entries}
    rendered_by_variant: dict[str, list[DigestEntry]] = {}
    metadata_by_variant: dict[str, DigestSummaryMetadata] = {}
    availability = OllamaAvailabilityCache(ollama_url)
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
                        options=job.options,
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
                                newsletter_type=job.canonical_newsletter_type,
                                profile_name=job.profile_name,
                                provider=job.provider,
                                model=model,
                                prompt_style=job.prompt_style,
                                status="cache",
                                summary_text=cached,
                                error_message=None,
                                input_char_count=input_char_count,
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
                                    newsletter_type=job.canonical_newsletter_type,
                                    profile_name=job.profile_name,
                                    provider=job.provider,
                                    model=model,
                                    prompt_style=job.prompt_style,
                                    status="legacy_cache",
                                    summary_text=legacy,
                                    error_message=None,
                                    input_char_count=input_char_count,
                                    variant_name=variant_name,
                                )
                            )
                            continue
                except Exception as exc:
                    logger.warning("Cache read failed for %s: %s", parsed.subject, exc)
            ok, msg = availability.check(model)
            if not ok:
                error_message = f"Model unavailable for profile {job.profile_name!r}: {msg}. Try: ollama pull {model}"
                logger.warning("%s", error_message)
                fallback = _fallback_entry(entry)
                rendered_entries.append(fallback)
                collector.record(
                    timed_result(
                        start=start,
                        message_key=job.message_key,
                        newsletter_type=job.canonical_newsletter_type,
                        profile_name=job.profile_name,
                        provider=job.provider,
                        model=model,
                        prompt_style=job.prompt_style,
                        status="error",
                        summary_text=fallback.summary,
                        error_message=error_message,
                        input_char_count=input_char_count,
                        variant_name=variant_name,
                    )
                )
                continue
            logger.info(
                "Ollama [%d/%d] summarising: %r (model=%s, profile=%s)",
                job_index,
                total_jobs,
                parsed.subject,
                model,
                job.profile_name,
            )
            try:
                summary = summarize_message(
                    classified,
                    ollama_url,
                    model,
                    max_chars,
                    timeout=job.timeout_seconds or 120,
                    prompt_style=job.prompt_style,
                    options=job.options,
                    temperature=job.temperature,
                    num_ctx=job.num_ctx,
                    quiet=quiet,
                )
            except Exception as exc:
                logger.warning("Summary failed for %s: %s", parsed.subject, exc)
                fallback = _fallback_entry(entry)
                rendered_entries.append(fallback)
                collector.record(
                    timed_result(
                        start=start,
                        message_key=job.message_key,
                        newsletter_type=job.canonical_newsletter_type,
                        profile_name=job.profile_name,
                        provider=job.provider,
                        model=model,
                        prompt_style=job.prompt_style,
                        status="fallback",
                        summary_text=fallback.summary,
                        error_message=str(exc),
                        input_char_count=input_char_count,
                        variant_name=variant_name,
                    )
                )
                continue
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
                        options=job.options,
                        summary_input_hash=summary_input_hash,
                        summary=summary,
                        created_at=_dt.datetime.now().astimezone(),
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to cache summary for %s: %s", parsed.subject, exc
                    )
            rendered_entries.append(
                DigestEntry(
                    classified=classified, summary=summary, summary_source="ollama"
                )
            )
            collector.record(
                timed_result(
                    start=start,
                    message_key=job.message_key,
                    newsletter_type=job.canonical_newsletter_type,
                    profile_name=job.profile_name,
                    provider=job.provider,
                    model=model,
                    prompt_style=job.prompt_style,
                    status="ollama",
                    summary_text=summary,
                    error_message=None,
                    input_char_count=input_char_count,
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
            summaries_cache=report.summaries_cache,
            summaries_fallback=report.summaries_fallback,
            summaries_errors=report.summaries_errors,
            selected_profiles=report.selected_profiles,
            output_variants=report.output_variants,
            routing_counts=tuple(
                DigestSummaryRouteStat(
                    newsletter_type=row.newsletter_type,
                    profile_name=row.profile_name,
                    model=row.model,
                    count=row.count,
                )
                for row in report.routing_counts
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
