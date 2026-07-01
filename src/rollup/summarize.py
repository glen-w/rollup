"""Local Ollama summarisation (optional)."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from rollup.models import ClassifiedMessage, DigestEntry, SummarySource

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


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


def build_prompt(classified: ClassifiedMessage, body_excerpt: str) -> str:
    p = classified.parsed
    template = _load_prompt(classified.newsletter_type)
    return template.format(
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
            return False, f"Model {model!r} not found in Ollama. Available: {models[:5]}"
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def summarize_message(
    classified: ClassifiedMessage,
    ollama_url: str,
    model: str,
    max_chars: int,
    timeout: int = 120,
) -> str:
    import requests

    excerpt = classified.parsed.body_text[:max_chars]
    prompt = build_prompt(classified, excerpt)
    resp = requests.post(
        ollama_url,
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("response", "").strip()


def apply_summaries(
    entries: list[DigestEntry],
    ollama_url: str,
    model: str,
    max_chars: int,
    allow_remote: bool,
    conn=None,
    rebuild: bool = False,
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
    for entry in entries:
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
                result.append(
                    DigestEntry(
                        classified=classified, summary=cached, summary_source="cache"
                    )
                )
                continue
        try:
            summary = summarize_message(classified, ollama_url, model, max_chars)
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
                logger.warning("Failed to cache summary for %s: %s", parsed.subject, exc)
        result.append(
            DigestEntry(
                classified=classified, summary=summary, summary_source="ollama"
            )
        )
    return result


def _fallback_entry(entry: DigestEntry) -> DigestEntry:
    parsed = entry.classified.parsed
    if parsed.preview:
        return DigestEntry(
            classified=entry.classified,
            summary=parsed.preview,
            summary_source="preview_fallback",
        )
    return DigestEntry(
        classified=entry.classified, summary=None, summary_source="none"
    )
