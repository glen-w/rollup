"""Shared Ollama streaming consumer with output and time guardrails."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from time import monotonic, perf_counter
from typing import Literal

StreamStopReason = Literal[
    "done",
    "provider_length",
    "local_char_cap",
    "local_wall_timeout",
    "parse_error",
    "http_error",
    "eof_without_done",
]

NON_CACHEABLE_STOP_REASONS: frozenset[StreamStopReason] = frozenset(
    {
        "local_char_cap",
        "local_wall_timeout",
        "parse_error",
        "http_error",
        "eof_without_done",
    }
)

PROGRESS_INTERVAL_CHARS = 500
PROGRESS_INTERVAL_SECONDS = 2.0


@dataclass(frozen=True)
class StreamResult:
    text: str
    stop_reason: StreamStopReason
    output_chars: int
    eval_count: int | None
    elapsed_seconds: float

    @property
    def truncated(self) -> bool:
        return self.stop_reason in {
            "local_char_cap",
            "local_wall_timeout",
            "provider_length",
        }


def is_stop_reason_cacheable(stop_reason: StreamStopReason) -> bool:
    return stop_reason not in NON_CACHEABLE_STOP_REASONS


def _resolve_done_stop_reason(data: dict[str, object]) -> StreamStopReason:
    done_reason = str(data.get("done_reason", "") or "")
    if done_reason == "length":
        return "provider_length"
    return "done"


_CLEAR_EOL = "\033[K"


def _write_progress(total_chars: int, eval_count: int | None) -> None:
    if eval_count is None:
        sys.stderr.write(f"\r  generating… {total_chars} chars{_CLEAR_EOL}")
    else:
        suffix = f", {eval_count} tokens"
        sys.stderr.write(f"\r  generated{suffix}{_CLEAR_EOL}\n")
    sys.stderr.flush()


def consume_ollama_stream(
    resp,
    *,
    max_output_chars: int,
    max_wall_seconds: float | None = None,
    show_progress: bool = False,
    progress_interval_chars: int = PROGRESS_INTERVAL_CHARS,
    progress_interval_seconds: float = PROGRESS_INTERVAL_SECONDS,
    started_at: float | None = None,
) -> StreamResult:
    """Read an Ollama streaming response with client-side limits."""
    start = started_at if started_at is not None else perf_counter()
    deadline = monotonic() + max_wall_seconds if max_wall_seconds is not None else None
    parts: list[str] = []
    stop_reason: StreamStopReason = "eof_without_done"
    eval_count: int | None = None
    last_progress_chars = 0
    last_progress_at = monotonic()
    should_close = False

    try:
        for line in resp.iter_lines(decode_unicode=True):
            if deadline is not None and monotonic() >= deadline:
                stop_reason = "local_wall_timeout"
                should_close = True
                break
            if line is None:
                continue
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                stop_reason = "parse_error"
                should_close = True
                break
            if not isinstance(data, dict):
                stop_reason = "parse_error"
                should_close = True
                break
            if data.get("error"):
                stop_reason = "http_error"
                should_close = True
                break
            chunk = data.get("response", "")
            if chunk:
                parts.append(str(chunk))
                total_chars = sum(len(part) for part in parts)
                if show_progress:
                    now = monotonic()
                    if (
                        total_chars - last_progress_chars >= progress_interval_chars
                        or now - last_progress_at >= progress_interval_seconds
                    ):
                        _write_progress(total_chars, None)
                        last_progress_chars = total_chars
                        last_progress_at = now
                if total_chars >= max_output_chars:
                    stop_reason = "local_char_cap"
                    should_close = True
                    break
            if data.get("done"):
                stop_reason = _resolve_done_stop_reason(data)
                raw_eval = data.get("eval_count")
                eval_count = int(raw_eval) if raw_eval is not None else None
                if show_progress:
                    total_chars = sum(len(part) for part in parts)
                    _write_progress(total_chars, eval_count)
                break
        else:
            if stop_reason == "eof_without_done":
                should_close = True
    finally:
        if should_close:
            close = getattr(resp, "close", None)
            if callable(close):
                close()

    text = "".join(parts)
    if len(text) > max_output_chars:
        text = text[:max_output_chars]
    return StreamResult(
        text=text,
        stop_reason=stop_reason,
        output_chars=len(text),
        eval_count=eval_count,
        elapsed_seconds=perf_counter() - start,
    )
