"""Whole-digest editorial QA review (report and apply modes)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from rollup.config import Config
from rollup.provider_errors import is_provider_call_error
from rollup.final_review_profiles import (
    FINAL_REVIEW_ESTIMATED_CHARS_PER_TOKEN,
    FINAL_REVIEW_RESERVED_OUTPUT_TOKENS,
    FinalReviewProfile,
    resolve_final_review_profile,
)
from rollup.models import (
    DigestEntry,
    DigestReport,
    DigestReviewCorpus,
    DigestReviewEntry,
    DigestSummaryMetadata,
    FinalReviewIssue,
    FinalReviewIssueType,
    FinalReviewPatch,
    FinalReviewResult,
    FinalReviewSeverity,
    FinalReviewSource,
    FinalReviewStatus,
)
from rollup.summarize import OllamaAvailabilityCache, validate_ollama_url

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts" / "final_review"
FINAL_REVIEW_PROMPT_VERSION = "final_review_v1"
FINAL_REVIEW_PROMPT_VERSION_APPLY = "final_review_v2_apply"
MAX_LINK_LABELS = 5
MAX_SUMMARY_CHARS = 1200
FINAL_REVIEW_SUMMARY_CHAR_STEPS = (1200, 600, 300, 150)

_VALID_ISSUE_TYPES = frozenset(
    {
        "style_drift",
        "duplication",
        "date_inconsistency",
        "heading_inconsistency",
        "link_issue",
        "metadata_mismatch",
        "possible_contradiction",
        "length_mismatch",
        "other",
    }
)
_VALID_SEVERITIES = frozenset({"minor", "major", "critical"})
_VALID_STATUSES = frozenset({"pass", "pass_with_warnings", "fail"})
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


class FinalReviewError(Exception):
    """Raised when final review configuration or execution is invalid."""


def _format_entry_date(entry: DigestEntry) -> str | None:
    dt = entry.classified.parsed.date_parsed
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M")


def _link_labels_for_entry(entry: DigestEntry) -> tuple[str, ...]:
    labels: list[str] = []
    for item in entry.classified.parsed.link_items[:MAX_LINK_LABELS]:
        label = (item.text or item.href or "").strip()
        if label:
            labels.append(label[:120])
    return tuple(labels)


def _truncate_summary(
    summary: str | None, *, max_chars: int = MAX_SUMMARY_CHARS
) -> str | None:
    if summary is None:
        return None
    if len(summary) <= max_chars:
        return summary
    return summary[: max_chars - 1] + "…"


def _summary_metadata_to_dict(
    metadata: DigestSummaryMetadata | None,
) -> dict | None:
    if metadata is None:
        return None
    return asdict(metadata)


def _corpus_entry_from_digest_entry(
    entry: DigestEntry, section: str, *, max_summary_chars: int = MAX_SUMMARY_CHARS
) -> DigestReviewEntry:
    parsed = entry.classified.parsed
    return DigestReviewEntry(
        entry_id=parsed.message_key,
        section=section,
        subject=parsed.subject,
        sender=parsed.sender,
        date=_format_entry_date(entry),
        newsletter_type=entry.classified.newsletter_type,
        read_time_minutes=parsed.read_time_minutes,
        summary_source=entry.summary_source,
        summary=_truncate_summary(entry.summary, max_chars=max_summary_chars),
        link_labels=_link_labels_for_entry(entry),
        source_key=parsed.source_key,
    )


def build_review_corpus(
    report: DigestReport, *, max_summary_chars: int = MAX_SUMMARY_CHARS
) -> DigestReviewCorpus:
    entries: list[DigestReviewEntry] = []
    for folder, folder_entries in sorted(report.dated_by_folder.items()):
        for item in folder_entries:
            for entry in _iter_digest_entries(item):
                entries.append(
                    _corpus_entry_from_digest_entry(
                        entry, folder, max_summary_chars=max_summary_chars
                    )
                )
    for item in report.undated:
        for entry in _iter_digest_entries(item):
            entries.append(
                _corpus_entry_from_digest_entry(
                    entry, "undated", max_summary_chars=max_summary_chars
                )
            )
    return DigestReviewCorpus(
        window_start=report.window_start.isoformat(),
        window_end=report.window_end.isoformat(),
        lookback_days=report.lookback_days,
        entry_count=len(entries),
        summary_metadata=_summary_metadata_to_dict(report.summary_metadata),
        entries=tuple(entries),
    )


def corpus_to_dict(corpus: DigestReviewCorpus) -> dict:
    return asdict(corpus)


def render_review_outline(corpus: DigestReviewCorpus) -> str:
    lines: list[str] = []
    current_section: str | None = None
    for entry in corpus.entries:
        if entry.section != current_section:
            current_section = entry.section
            lines.append(f"## {current_section}")
        lines.append(f"- [{entry.entry_id}] {entry.subject}")
    return "\n".join(lines)


def _entry_fingerprint(entry: DigestEntry, section: str) -> dict:
    summary = entry.summary or ""
    return {
        "entry_id": entry.classified.parsed.message_key,
        "content_hash": entry.classified.parsed.content_hash,
        "section": section,
        "newsletter_type": entry.classified.newsletter_type,
        "summary_source": entry.summary_source,
        "summary_hash": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
    }


def _iter_digest_entries(item) -> list:
    """Flatten DigestGroup to DigestEntry list; pass DigestEntry through."""
    if hasattr(item, "entries") and not hasattr(item, "classified"):
        return list(item.entries)
    return [item]


def compute_digest_fingerprint(report: DigestReport) -> str:
    """Host-truth digest fingerprint.

    Canonical representation: sorted folder keys; items in report order within
    each folder; each entry via ``_entry_fingerprint``; undated labeled
    ``\"undated\"``; ``json.dumps(..., sort_keys=True, separators=(",", ":"))``;
    SHA-256 hex. Computed once at the start of ``execute_final_review`` before
    corpus fit / cache lookup / model call. Model echo must not be synthesised.
    """
    parts: list[dict] = []
    for folder, entries in sorted(report.dated_by_folder.items()):
        for item in entries:
            for entry in _iter_digest_entries(item):
                parts.append(_entry_fingerprint(entry, folder))
    for item in report.undated:
        for entry in _iter_digest_entries(item):
            parts.append(_entry_fingerprint(entry, "undated"))
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_prompt_file(name: str) -> str:
    path = PROMPTS_DIR / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def build_final_review_prompt(
    corpus: DigestReviewCorpus,
    profile: FinalReviewProfile,
    *,
    mode: str = "report",
    digest_fingerprint: str | None = None,
) -> str:
    base = _load_prompt_file("_base.txt")
    style = _load_prompt_file(f"{profile.prompt_style}.txt")
    schema = _load_prompt_file("_schema.json")
    outline = render_review_outline(corpus)
    corpus_json = json.dumps(corpus_to_dict(corpus), indent=2, sort_keys=True)
    prompt = (
        f"{base}\n\n{style}\n\n"
        f"JSON schema:\n{schema}\n\n"
        f"Digest outline:\n{outline}\n\n"
        f"Digest corpus (JSON):\n{corpus_json}"
    )
    if mode == "apply":
        fp = digest_fingerprint or ""
        prompt += (
            "\n\nApply mode instructions:\n"
            "- You MAY propose summary-only patches for safe_auto_fix issues.\n"
            "- Each patch MUST include issue_id, entry_id, field=\"summary\", "
            "replacement, and rationale.\n"
            "- Do NOT invent facts, URLs, or named entities absent from the original.\n"
            "- Keep edits small; preserve links and quoted spans.\n"
            "- Echo digest_fingerprint exactly in your JSON response.\n"
            f"- digest_fingerprint: {fp}\n"
            "- Never patch group: entries, titles, dates, or metadata.\n"
        )
    return prompt


def estimate_prompt_tokens(prompt: str) -> int:
    return max(1, len(prompt) // FINAL_REVIEW_ESTIMATED_CHARS_PER_TOKEN)


def prompt_exceeds_context(prompt: str, profile: FinalReviewProfile) -> bool:
    if profile.num_ctx is None:
        return False
    budget = profile.num_ctx - FINAL_REVIEW_RESERVED_OUTPUT_TOKENS
    return estimate_prompt_tokens(prompt) > budget


def build_fitted_final_review_prompt(
    report: DigestReport,
    profile: FinalReviewProfile,
    *,
    mode: str = "report",
    digest_fingerprint: str | None = None,
) -> tuple[DigestReviewCorpus, str, int]:
    """Shrink review summaries until the prompt fits the model context window."""
    last_corpus = build_review_corpus(report)
    last_prompt = build_final_review_prompt(
        last_corpus, profile, mode=mode, digest_fingerprint=digest_fingerprint
    )
    last_limit = MAX_SUMMARY_CHARS
    for summary_limit in FINAL_REVIEW_SUMMARY_CHAR_STEPS:
        corpus = build_review_corpus(report, max_summary_chars=summary_limit)
        prompt = build_final_review_prompt(
            corpus, profile, mode=mode, digest_fingerprint=digest_fingerprint
        )
        last_corpus, last_prompt, last_limit = corpus, prompt, summary_limit
        if not prompt_exceeds_context(prompt, profile):
            if summary_limit < MAX_SUMMARY_CHARS:
                logger.info(
                    "Final review: reduced summary excerpt to %d chars to fit context",
                    summary_limit,
                )
            return corpus, prompt, summary_limit
    return last_corpus, last_prompt, last_limit


def compute_review_input_hash(
    corpus: DigestReviewCorpus,
    profile: FinalReviewProfile,
    prompt: str,
    *,
    prompt_version: str = FINAL_REVIEW_PROMPT_VERSION,
) -> str:
    payload = json.dumps(
        {
            "corpus": corpus_to_dict(corpus),
            "profile": profile.name,
            "prompt_style": profile.prompt_style,
            "prompt": prompt,
            "prompt_version": prompt_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _extract_json_object(raw: str) -> str:
    stripped = raw.strip()
    fence_match = _JSON_FENCE_RE.search(stripped)
    if fence_match:
        return fence_match.group(1).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


def _coerce_issue_type(value: object) -> FinalReviewIssueType:
    if isinstance(value, str) and value in _VALID_ISSUE_TYPES:
        return value  # type: ignore[return-value]
    return "other"


def _coerce_severity(value: object) -> FinalReviewSeverity | None:
    if isinstance(value, str) and value in _VALID_SEVERITIES:
        return value  # type: ignore[return-value]
    return None


def _coerce_status(value: object) -> FinalReviewStatus:
    if isinstance(value, str) and value in _VALID_STATUSES:
        return value  # type: ignore[return-value]
    return "fail"


def _parse_issue(raw: dict) -> FinalReviewIssue | None:
    if not isinstance(raw, dict):
        return None
    severity = _coerce_severity(raw.get("severity"))
    if severity is None:
        severity = "major"
    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        return None
    location = raw.get("location")
    if not isinstance(location, str):
        location = "unknown"
    entry_id = raw.get("entry_id")
    if entry_id is not None and not isinstance(entry_id, str):
        entry_id = None
    suggested_fix = raw.get("suggested_fix")
    if suggested_fix is not None and not isinstance(suggested_fix, str):
        suggested_fix = None
    # Fail closed: only literal JSON boolean true authorises auto-fix.
    raw_safe = raw.get("safe_auto_fix", False)
    safe_auto_fix = raw_safe is True
    issue_id = raw.get("issue_id")
    if issue_id is not None and not isinstance(issue_id, str):
        issue_id = None
    if isinstance(issue_id, str) and len(issue_id) > 256:
        issue_id = None
    return FinalReviewIssue(
        severity=severity,
        type=_coerce_issue_type(raw.get("type")),
        location=location,
        entry_id=entry_id,
        description=description.strip(),
        suggested_fix=suggested_fix.strip() if suggested_fix else None,
        safe_auto_fix=safe_auto_fix,
        issue_id=issue_id.strip() if issue_id else None,
    )


def _parse_patch(raw: dict) -> FinalReviewPatch | None:
    if not isinstance(raw, dict):
        return None
    entry_id = raw.get("entry_id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        return None
    field = raw.get("field", "summary")
    if field != "summary":
        return None
    replacement = raw.get("replacement")
    if not isinstance(replacement, str) or not replacement.strip():
        return None
    rationale = raw.get("rationale") or raw.get("reason") or ""
    if not isinstance(rationale, str):
        rationale = ""
    issue_id = raw.get("issue_id")
    if issue_id is not None and not isinstance(issue_id, str):
        issue_id = None
    # Reject patches targeting synthetic group cards.
    if entry_id.startswith("group:"):
        return None
    if len(entry_id.strip()) > 256:
        return None
    if isinstance(issue_id, str) and len(issue_id.strip()) > 256:
        return None
    return FinalReviewPatch(
        entry_id=entry_id.strip(),
        field="summary",
        replacement=replacement.strip(),
        rationale=rationale.strip(),
        issue_id=issue_id.strip() if issue_id else None,
    )


def _error_result(
    *,
    message: str,
    profile_name: str,
    model: str,
    generated_at: datetime,
    digest_fingerprint: str,
    review_input_hash: str,
) -> FinalReviewResult:
    return FinalReviewResult(
        overall_status="fail",
        safe_to_publish=False,
        issues=(
            FinalReviewIssue(
                severity="critical",
                type="other",
                location="final_review",
                entry_id=None,
                description=message,
                suggested_fix=None,
                safe_auto_fix=False,
            ),
        ),
        patches=(),
        review_source="error",
        profile_name=profile_name,
        model=model,
        prompt_version=FINAL_REVIEW_PROMPT_VERSION,
        generated_at=generated_at,
        digest_fingerprint=digest_fingerprint,
        review_input_hash=review_input_hash,
    )


def parse_final_review_response(
    raw: str,
    *,
    profile_name: str,
    model: str,
    generated_at: datetime,
    digest_fingerprint: str,
    review_input_hash: str,
    review_source: FinalReviewSource = "ollama",
    prompt_chars: int | None = None,
    num_ctx: int | None = None,
) -> FinalReviewResult:
    stripped = raw.strip()
    if not stripped:
        detail = "Model returned an empty response."
        if prompt_chars is not None and num_ctx is not None:
            est_tokens = max(1, prompt_chars // FINAL_REVIEW_ESTIMATED_CHARS_PER_TOKEN)
            detail = (
                f"{detail} Prompt is ~{prompt_chars} chars (~{est_tokens} est. tokens) "
                f"with num_ctx={num_ctx}; the review input likely exceeds the context window."
            )
        return _error_result(
            message=detail,
            profile_name=profile_name,
            model=model,
            generated_at=generated_at,
            digest_fingerprint=digest_fingerprint,
            review_input_hash=review_input_hash,
        )
    try:
        payload = json.loads(_extract_json_object(raw))
    except json.JSONDecodeError as exc:
        preview = stripped[:120].replace("\n", " ")
        message = f"Failed to parse final review JSON: {exc}"
        if preview:
            message = f"{message} Response started with: {preview!r}"
        if prompt_chars is not None and num_ctx is not None:
            est_tokens = max(1, prompt_chars // FINAL_REVIEW_ESTIMATED_CHARS_PER_TOKEN)
            message = (
                f"{message} Prompt is ~{prompt_chars} chars (~{est_tokens} est. tokens) "
                f"with num_ctx={num_ctx}."
            )
        return _error_result(
            message=message,
            profile_name=profile_name,
            model=model,
            generated_at=generated_at,
            digest_fingerprint=digest_fingerprint,
            review_input_hash=review_input_hash,
        )
    if not isinstance(payload, dict):
        return _error_result(
            message="Final review response was not a JSON object.",
            profile_name=profile_name,
            model=model,
            generated_at=generated_at,
            digest_fingerprint=digest_fingerprint,
            review_input_hash=review_input_hash,
        )

    issues: list[FinalReviewIssue] = []
    raw_issues = payload.get("issues", [])
    if isinstance(raw_issues, list):
        for item in raw_issues:
            issue = _parse_issue(item)
            if issue is not None:
                issues.append(issue)

    overall_status = _coerce_status(payload.get("overall_status"))
    raw_safe_publish = payload.get("safe_to_publish")
    # Fail closed: only literal JSON boolean true is safe to publish.
    safe_to_publish = raw_safe_publish is True

    patches: list[FinalReviewPatch] = []
    raw_patches = payload.get("patches", [])
    if raw_patches is None:
        raw_patches = []
    if isinstance(raw_patches, list):
        for item in raw_patches:
            patch = _parse_patch(item)
            if patch is not None:
                patches.append(patch)

    # Model schema uses digest_fingerprint as the echo field.
    echoed = payload.get("echoed_digest_fingerprint")
    if echoed is None:
        echoed = payload.get("digest_fingerprint")
    if echoed is not None and not isinstance(echoed, str):
        echoed = None
    if isinstance(echoed, str) and not echoed.strip():
        echoed = None

    return FinalReviewResult(
        overall_status=overall_status,
        safe_to_publish=safe_to_publish,
        issues=tuple(issues),
        patches=tuple(patches),
        review_source=review_source,
        profile_name=profile_name,
        model=model,
        prompt_version=FINAL_REVIEW_PROMPT_VERSION,
        generated_at=generated_at,
        digest_fingerprint=digest_fingerprint,
        review_input_hash=review_input_hash,
        echoed_digest_fingerprint=echoed,
    )


def call_final_review_model(
    prompt: str,
    *,
    ollama_url: str,
    profile: FinalReviewProfile,
    quiet: bool = False,
) -> str:
    import requests

    from rollup.final_review_profiles import FINAL_REVIEW_MAX_OUTPUT_CHARS
    from rollup.ollama_stream import consume_ollama_stream

    payload_options = dict(profile.options)
    payload_options.setdefault("temperature", profile.temperature)
    if profile.num_ctx is not None:
        payload_options.setdefault("num_ctx", profile.num_ctx)
    use_stream = not quiet
    resp = requests.post(
        ollama_url,
        json={
            "model": profile.model,
            "prompt": prompt,
            "stream": use_stream,
            "options": payload_options,
        },
        timeout=profile.timeout_seconds,
        stream=use_stream,
    )
    resp.raise_for_status()
    if use_stream:
        stream_result = consume_ollama_stream(
            resp,
            max_output_chars=FINAL_REVIEW_MAX_OUTPUT_CHARS,
            max_wall_seconds=float(profile.timeout_seconds),
            show_progress=not quiet,
        )
        return stream_result.text.strip()
    data = resp.json()
    if data.get("error"):
        return ""
    return str(data.get("response", "")).strip()


def _result_from_dict(data: dict) -> FinalReviewResult:
    issues = tuple(
        FinalReviewIssue(
            severity=_coerce_severity(issue.get("severity")) or "major",
            type=_coerce_issue_type(issue.get("type")),
            location=str(issue.get("location", "unknown")),
            entry_id=issue.get("entry_id"),
            description=str(issue.get("description", "")),
            suggested_fix=issue.get("suggested_fix"),
            safe_auto_fix=issue.get("safe_auto_fix") is True,
            issue_id=issue.get("issue_id") if isinstance(issue.get("issue_id"), str) else None,
        )
        for issue in data.get("issues", [])
        if isinstance(issue, dict)
    )
    patches: list[FinalReviewPatch] = []
    raw_patches = data.get("patches", [])
    if raw_patches is None:
        raw_patches = []
    for item in raw_patches or []:
        if isinstance(item, dict):
            patch = _parse_patch(item)
            if patch is not None:
                patches.append(patch)
    generated_raw = data.get("generated_at")
    if isinstance(generated_raw, str):
        generated_at = datetime.fromisoformat(generated_raw)
    else:
        generated_at = datetime.now().astimezone()
    # Cached payloads store host fingerprint separately from model echo.
    # Never synthesise echo from digest_fingerprint.
    echoed = data.get("echoed_digest_fingerprint")
    if echoed is not None and not isinstance(echoed, str):
        echoed = None
    if isinstance(echoed, str) and not echoed.strip():
        echoed = None
    raw_safe = data.get("safe_to_publish")
    return FinalReviewResult(
        overall_status=_coerce_status(data.get("overall_status")),
        safe_to_publish=raw_safe is True,
        issues=issues,
        patches=tuple(patches),
        review_source="cache",
        profile_name=str(data.get("profile_name", "")),
        model=str(data.get("model", "")),
        prompt_version=str(
            data.get("prompt_version", FINAL_REVIEW_PROMPT_VERSION)
        ),
        generated_at=generated_at,
        digest_fingerprint=str(data.get("digest_fingerprint", "")),
        review_input_hash=str(data.get("review_input_hash", "")),
        echoed_digest_fingerprint=echoed,
        review_mode=str(data.get("review_mode", "report")),
    )


def _result_from_cached_json(
    result_json: str,
    *,
    profile_name: str,
    model: str,
    generated_at: datetime,
    digest_fingerprint: str,
    review_input_hash: str,
) -> FinalReviewResult:
    data = json.loads(result_json)
    if not isinstance(data, dict):
        return _error_result(
            message="Cached final review payload was not a JSON object.",
            profile_name=profile_name,
            model=model,
            generated_at=generated_at,
            digest_fingerprint=digest_fingerprint,
            review_input_hash=review_input_hash,
        )
    result = _result_from_dict(data)
    return FinalReviewResult(
        overall_status=result.overall_status,
        safe_to_publish=result.safe_to_publish,
        issues=result.issues,
        patches=result.patches,
        review_source="cache",
        profile_name=result.profile_name or profile_name,
        model=result.model or model,
        prompt_version=result.prompt_version,
        generated_at=result.generated_at,
        # Host truth is always the current digest fingerprint.
        digest_fingerprint=digest_fingerprint,
        review_input_hash=result.review_input_hash or review_input_hash,
        echoed_digest_fingerprint=result.echoed_digest_fingerprint,
        review_mode=result.review_mode,
    )


def execute_final_review(
    report: DigestReport,
    config: Config,
    *,
    conn=None,
    quiet: bool = True,
) -> FinalReviewResult:
    generated_at = datetime.now().astimezone()
    profile = resolve_final_review_profile(
        config.final_review_profile,
        model_override=config.final_review_model,
    )
    mode = config.final_review_mode if config.final_review_mode in ("report", "apply") else "report"
    prompt_version = (
        FINAL_REVIEW_PROMPT_VERSION_APPLY if mode == "apply" else FINAL_REVIEW_PROMPT_VERSION
    )
    digest_fingerprint = compute_digest_fingerprint(report)
    corpus, prompt, summary_limit = build_fitted_final_review_prompt(
        report,
        profile,
        mode=mode,
        digest_fingerprint=digest_fingerprint,
    )
    review_input_hash = compute_review_input_hash(
        corpus, profile, prompt, prompt_version=prompt_version
    )

    if prompt_exceeds_context(prompt, profile):
        est_tokens = estimate_prompt_tokens(prompt)
        return _error_result(
            message=(
                "Final review prompt exceeds the model context window even after "
                f"shrinking summaries to {summary_limit} chars "
                f"(~{len(prompt)} prompt chars, ~{est_tokens} est. tokens, "
                f"num_ctx={profile.num_ctx}). Try a smaller lookback window or "
                "a model with a larger context."
            ),
            profile_name=profile.name,
            model=profile.model,
            generated_at=generated_at,
            digest_fingerprint=digest_fingerprint,
            review_input_hash=review_input_hash,
        )

    if not config.rebuild_final_review and conn is not None:
        from rollup.state import get_final_review_generation

        cached = get_final_review_generation(
            conn,
            digest_fingerprint=digest_fingerprint,
            review_input_hash=review_input_hash,
            provider=profile.provider,
            profile_name=profile.name,
            model=profile.model,
            prompt_version=prompt_version,
            temperature=profile.temperature,
            num_ctx=profile.num_ctx,
            options=profile.options,
        )
        if cached is not None:
            logger.info("Final review: cache hit")
            cached_result = _result_from_cached_json(
                cached,
                profile_name=profile.name,
                model=profile.model,
                generated_at=generated_at,
                digest_fingerprint=digest_fingerprint,
                review_input_hash=review_input_hash,
            )
            return replace(cached_result, review_mode=mode)

    validate_ollama_url(config.ollama_url, config.allow_remote_ollama)
    availability = OllamaAvailabilityCache(config.ollama_url)
    ok, message = availability.check(profile.model)
    if not ok:
        return _error_result(
            message=f"Ollama model unavailable: {message}",
            profile_name=profile.name,
            model=profile.model,
            generated_at=generated_at,
            digest_fingerprint=digest_fingerprint,
            review_input_hash=review_input_hash,
        )

    try:
        raw = call_final_review_model(
            prompt,
            ollama_url=config.ollama_url,
            profile=profile,
            quiet=quiet,
        )
    except Exception as exc:
        if not is_provider_call_error(exc):
            raise
        logger.warning("Final review model call failed: %s", exc)
        return _error_result(
            message=f"Final review model call failed: {exc}",
            profile_name=profile.name,
            model=profile.model,
            generated_at=generated_at,
            digest_fingerprint=digest_fingerprint,
            review_input_hash=review_input_hash,
        )

    result = parse_final_review_response(
        raw,
        profile_name=profile.name,
        model=profile.model,
        generated_at=generated_at,
        digest_fingerprint=digest_fingerprint,
        review_input_hash=review_input_hash,
        review_source="ollama",
        prompt_chars=len(prompt),
        num_ctx=profile.num_ctx,
    )
    result = replace(
        result,
        prompt_version=prompt_version,
        review_mode=mode,
        # Preserve model/cache echo only; never synthesise from host fingerprint.
        echoed_digest_fingerprint=result.echoed_digest_fingerprint,
    )

    if conn is not None and result.review_source == "ollama":
        from rollup.state import store_final_review_generation

        store_final_review_generation(
            conn,
            digest_fingerprint=digest_fingerprint,
            review_input_hash=review_input_hash,
            provider=profile.provider,
            profile_name=profile.name,
            model=profile.model,
            prompt_version=prompt_version,
            temperature=profile.temperature,
            num_ctx=profile.num_ctx,
            options=profile.options,
            result_json=final_review_result_to_json(result),
            created_at=generated_at,
        )

    return result


def final_review_result_to_dict(result: FinalReviewResult) -> dict:
    data = asdict(result)
    data["generated_at"] = result.generated_at.isoformat()
    return data


def final_review_result_to_json(result: FinalReviewResult) -> str:
    return json.dumps(final_review_result_to_dict(result), indent=2, sort_keys=True)


def write_final_review_report(result: FinalReviewResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(final_review_result_to_json(result), encoding="utf-8")


def count_final_review_issues_by_severity(
    result: FinalReviewResult,
) -> dict[str, int]:
    counts = {"critical": 0, "major": 0, "minor": 0}
    for issue in result.issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    return counts


def format_final_review_digest_summary(result: FinalReviewResult) -> str:
    """Plain-text summary for embedding in digest run details."""
    counts = count_final_review_issues_by_severity(result)
    lines = [
        f"Status: {result.overall_status}",
        f"Safe to publish: {str(result.safe_to_publish).lower()}",
        (
            f"Issues: {counts['critical']} critical, "
            f"{counts['major']} major, {counts['minor']} minor"
        ),
        (
            f"Source: {result.review_source} "
            f"({result.profile_name} / {result.model})"
        ),
    ]
    if result.issues:
        lines.append("Notable issues:")
        for issue in result.issues[:5]:
            lines.append(
                f"- [{issue.severity}] {issue.location}: {issue.description}"
            )
        if len(result.issues) > 5:
            lines.append(f"- …and {len(result.issues) - 5} more")
    return "\n".join(lines)


def print_final_review_summary(result: FinalReviewResult, path: Path) -> None:
    counts = count_final_review_issues_by_severity(result)
    print(
        f"Final review: {result.overall_status} | "
        f"safe_to_publish={str(result.safe_to_publish).lower()} | "
        f"{counts['critical']} critical, {counts['major']} major, {counts['minor']} minor"
    )
    print(f"Report: {path}")
