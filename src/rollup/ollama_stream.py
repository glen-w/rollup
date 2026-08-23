"""Shared Ollama streaming consumer with output and time guardrails.

Provider-neutral types live in ``stream_result``; this module keeps the Ollama
stream consumer and re-exports shared types for backward compatibility.
"""

from __future__ import annotations

from rollup.llm_client import consume_ollama_stream
from rollup.stream_result import (
    NON_CACHEABLE_STOP_REASONS,
    StreamResult,
    StreamStopReason,
    is_stop_reason_cacheable,
)

PROGRESS_INTERVAL_CHARS = 500
PROGRESS_INTERVAL_SECONDS = 2.0

__all__ = [
    "NON_CACHEABLE_STOP_REASONS",
    "PROGRESS_INTERVAL_CHARS",
    "PROGRESS_INTERVAL_SECONDS",
    "StreamResult",
    "StreamStopReason",
    "consume_ollama_stream",
    "is_stop_reason_cacheable",
]
