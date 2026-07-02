"""Whole-digest editorial QA review (report-only in Phase 1)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from rollup.config import Config
from rollup.final_review_profiles import FinalReviewProfile, resolve_final_review_profile
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
MAX_LINK_LABELS = 5
MAX_SUMMARY_CHARS = 4000

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


def _truncate_summary(summary: str | None) -> str | None:
    if summary is None:
        return None
    if len(summary) <= MAX_SUMMARY_CHARS:
        return summary
    return summary[: MAX_SUMMARY_CHARS - 1] + "…"


def _summary_metadata_to_dict(
    metadata: DigestSummaryMetadata | None,
) -> dict | None:
    if metadata is None:
        return None
    return asdict(metadata)


def _corpus_entry_from_digest_entry(
    entry: DigestEntry, section: str
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
        summary=_truncate_summary(entry.summary),
        link_labels=_link_labels_for_entry(entry),
    )


def build_review_corpus(report: DigestReport) -> DigestReviewCorpus:
    entries: list[DigestReviewEntry] = []
    for folder, folder_entries in sorted(report.dated_by_folder.items()):
        for entry in folder_entries:
            entries.append(_corpus_entry_from_digest_entry(entry, folder))
    for entry in report.undated:
        entries.append(_corpus_entry_from_digest_entry(entry, "undated"))
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


def compute_digest_fingerprint(report: DigestReport) -> str:
    parts: list[dict] = []
    for folder, entries in sorted(report.dated_by_folder.items()):
        for entry in entries:
            parts.append(_entry_fingerprint(entry, folder))
    for entry in report.undated:
        parts.append(_entry_fingerprint(entry, "undated"))
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_prompt_file(name: str) -> str:
    path = PROMPTS_DIR / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def build_final_review_prompt(
    corpus: DigestReviewCorpus, profile: FinalReviewProfile
) -> str:
    base = _load_prompt_file("_base.txt")
    style = _load_prompt_file(f"{profile.prompt_style}.txt")
    schema = _load_prompt_file("_schema.json")
    outline = render_review_outline(corpus)
    corpus_json = json.dumps(corpus_to_dict(corpus), indent=2, sort_keys=True)
    return (
        f"{base}\n\n{style}\n\n"
        f"JSON schema:\n{schema}\n\n"
        f"Digest outline:\n{outline}\n\n"
        f"Digest corpus (JSON):\n{corpus_json}"
    )


def compute_review_input_hash(
    corpus: DigestReviewCorpus,
    profile: FinalReviewProfile,
    prompt: str,
) -> str:
    payload = json.dumps(
        {
            "corpus": corpus_to_dict(corpus),
            "profile": profile.name,
            "prompt_style": profile.prompt_style,
            "prompt": prompt,
            "prompt_version": FINAL_REVIEW_PROMPT_VERSION,
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
    safe_auto_fix = bool(raw.get("safe_auto_fix", False))
    return FinalReviewIssue(
        severity=severity,
        type=_coerce_issue_type(raw.get("type")),
        location=location,
        entry_id=entry_id,
        description=description.strip(),
        suggested_fix=suggested_fix.strip() if suggested_fix else None,
        safe_auto_fix=safe_auto_fix,
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
) -> FinalReviewResult:
    try:
        payload = json.loads(_extract_json_object(raw))
    except json.JSONDecodeError as exc:
        return _error_result(
            message=f"Failed to parse final review JSON: {exc}",
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
    safe_to_publish = payload.get("safe_to_publish")
    if not isinstance(safe_to_publish, bool):
        safe_to_publish = overall_status == "pass"

    return FinalReviewResult(
        overall_status=overall_status,
        safe_to_publish=safe_to_publish,
        issues=tuple(issues),
        patches=(),
        review_source=review_source,
        profile_name=profile_name,
        model=model,
        prompt_version=FINAL_REVIEW_PROMPT_VERSION,
        generated_at=generated_at,
        digest_fingerprint=digest_fingerprint,
        review_input_hash=review_input_hash,
    )


def call_final_review_model(
    prompt: str,
    *,
    ollama_url: str,
    profile: FinalReviewProfile,
    quiet: bool = False,
) -> str:
    import requests

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
        parts: list[str] = []
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            data = json.loads(line)
            chunk = data.get("response", "")
            if chunk:
                parts.append(chunk)
            if data.get("done"):
                break
        return "".join(parts).strip()
    data = resp.json()
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
            safe_auto_fix=bool(issue.get("safe_auto_fix", False)),
        )
        for issue in data.get("issues", [])
        if isinstance(issue, dict)
    )
    generated_raw = data.get("generated_at")
    if isinstance(generated_raw, str):
        generated_at = datetime.fromisoformat(generated_raw)
    else:
        generated_at = datetime.now().astimezone()
    return FinalReviewResult(
        overall_status=_coerce_status(data.get("overall_status")),
        safe_to_publish=bool(data.get("safe_to_publish", False)),
        issues=issues,
        patches=(),
        review_source="cache",
        profile_name=str(data.get("profile_name", "")),
        model=str(data.get("model", "")),
        prompt_version=str(
            data.get("prompt_version", FINAL_REVIEW_PROMPT_VERSION)
        ),
        generated_at=generated_at,
        digest_fingerprint=str(data.get("digest_fingerprint", "")),
        review_input_hash=str(data.get("review_input_hash", "")),
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
        patches=(),
        review_source="cache",
        profile_name=result.profile_name or profile_name,
        model=result.model or model,
        prompt_version=result.prompt_version,
        generated_at=result.generated_at,
        digest_fingerprint=result.digest_fingerprint or digest_fingerprint,
        review_input_hash=result.review_input_hash or review_input_hash,
    )


def execute_final_review(
    report: DigestReport,
    config: Config,
    *,
    conn=None,
) -> FinalReviewResult:
    generated_at = datetime.now().astimezone()
    profile = resolve_final_review_profile(
        config.final_review_profile,
        model_override=config.final_review_model,
    )
    corpus = build_review_corpus(report)
    prompt = build_final_review_prompt(corpus, profile)
    digest_fingerprint = compute_digest_fingerprint(report)
    review_input_hash = compute_review_input_hash(corpus, profile, prompt)

    if not config.rebuild_final_review and conn is not None:
        from rollup.state import get_final_review_generation

        cached = get_final_review_generation(
            conn,
            digest_fingerprint=digest_fingerprint,
            review_input_hash=review_input_hash,
            provider=profile.provider,
            profile_name=profile.name,
            model=profile.model,
            prompt_version=FINAL_REVIEW_PROMPT_VERSION,
            temperature=profile.temperature,
            num_ctx=profile.num_ctx,
            options=profile.options,
        )
        if cached is not None:
            logger.info("Final review: cache hit")
            return _result_from_cached_json(
                cached,
                profile_name=profile.name,
                model=profile.model,
                generated_at=generated_at,
                digest_fingerprint=digest_fingerprint,
                review_input_hash=review_input_hash,
            )

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
            quiet=config.quiet,
        )
    except Exception as exc:
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
            prompt_version=FINAL_REVIEW_PROMPT_VERSION,
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
