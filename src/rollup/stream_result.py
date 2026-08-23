"""Provider-neutral streaming completion results and stop reasons."""

from __future__ import annotations

from dataclasses import dataclass
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
