"""Grounded Ask over FTS hits. No conversation memory."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from rollup.corpus import ASK_EXCERPT_CHARS, ASK_TOP_K, CorpusHit

logger = logging.getLogger(__name__)

REFUSE_MESSAGE = "No matching items in the Rollup cache."
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
ASK_PROMPT_PATH = PROMPTS_DIR / "ask.txt"


@dataclass(frozen=True)
class AskResult:
    answer: str
    citations: tuple[str, ...]
    hits: tuple[CorpusHit, ...]
    refused: bool
    llm_used: bool
    error: str | None = None


def load_ask_prompt() -> str:
    return ASK_PROMPT_PATH.read_text(encoding="utf-8")


def build_ask_prompt(question: str, hits: tuple[CorpusHit, ...]) -> str:
    excerpts = []
    for hit in hits[:ASK_TOP_K]:
        body = (hit.snippet or hit.summary or hit.title)[:ASK_EXCERPT_CHARS]
        excerpts.append(
            {
                "message_key": hit.message_key,
                "title": hit.title,
                "folder": hit.folder,
                "excerpt": body,
            }
        )
    return (
        f"{load_ask_prompt().strip()}\n\n"
        f"Question:\n{question.strip()}\n\n"
        f"Excerpts:\n{json.dumps(excerpts, ensure_ascii=False)}\n"
    )


def parse_ask_answer(raw: str, allowed_keys: set[str]) -> tuple[str, tuple[str, ...]] | None:
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    answer = data.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return None
    cites_raw = data.get("citations") or []
    if not isinstance(cites_raw, list):
        cites_raw = []
    citations = tuple(
        key
        for key in (str(item).strip() for item in cites_raw)
        if key and key in allowed_keys
    )
    return answer.strip(), citations


def answer_question(
    question: str,
    hits: tuple[CorpusHit, ...],
    *,
    complete: Callable[[str], str] | None = None,
) -> AskResult:
    """Answer from FTS hits only. k=0 always refuses with a fixed message."""
    if not hits:
        return AskResult(
            answer=REFUSE_MESSAGE,
            citations=(),
            hits=(),
            refused=True,
            llm_used=False,
        )
    if complete is None:
        return AskResult(
            answer="Ask requires --ollama (or sticky ollama=true). Matching excerpts only.",
            citations=tuple(h.message_key for h in hits),
            hits=hits,
            refused=False,
            llm_used=False,
        )
    allowed = {h.message_key for h in hits}
    try:
        raw = complete(build_ask_prompt(question, hits))
    except Exception as exc:
        logger.warning("Ask LLM failed: %s", exc)
        return AskResult(
            answer="Ask failed; showing matching excerpts only.",
            citations=tuple(h.message_key for h in hits),
            hits=hits,
            refused=False,
            llm_used=False,
            error=type(exc).__name__,
        )
    parsed = parse_ask_answer(raw if isinstance(raw, str) else "", allowed)
    if parsed is None:
        return AskResult(
            answer="Ask returned an unusable answer; showing matching excerpts only.",
            citations=tuple(h.message_key for h in hits),
            hits=hits,
            refused=False,
            llm_used=True,
        )
    answer, citations = parsed
    return AskResult(
        answer=answer,
        citations=citations,
        hits=hits,
        refused=False,
        llm_used=True,
    )


def make_complete(config: Any) -> Callable[[str], str] | None:
    """Return ``complete(prompt) -> str`` when LLM is enabled, else None."""
    if getattr(config, "no_ollama", True):
        return None
    from rollup.llm_client import CompletionRequest, get_llm_client

    def complete(prompt: str) -> str:
        client = get_llm_client(
            config.llm_provider,
            ollama_url=config.ollama_url,
            allow_remote=config.allow_remote_ollama,
            llm_api_base=config.llm_api_base,
        )
        model = config.llm_model or config.ollama_model
        result = client.complete(
            CompletionRequest(
                model=model,
                prompt=prompt,
                stream=False,
                timeout_seconds=60.0,
                temperature=0.1,
                response_format_json=True,
            ),
            max_output_chars=4_000,
            show_progress=False,
        )
        return result.text

    return complete
