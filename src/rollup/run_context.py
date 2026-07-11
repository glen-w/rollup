"""Run identity, timing, and structured event log — not a mutable stats bag."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from rollup.clock import Clock, DEFAULT_CLOCK

RunMode = Literal["manual", "cron"]
RunStatus = Literal["success", "partial", "failure", "dry_run"]


@dataclass(frozen=True)
class RunEvent:
    """Append-only structured event for diagnostics (not email content)."""

    code: str
    message: str
    level: Literal["info", "warning", "error"] = "info"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunContext:
    """Identity and timing for a single digest run.

    Stage outputs and counts live in typed stage results aggregated by
    pipeline.py — not here.
    """

    run_id: str
    run_start_time: datetime
    mode: RunMode
    events: list[RunEvent] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        mode: RunMode = "manual",
        *,
        clock: Clock | None = None,
        run_id: str | None = None,
    ) -> RunContext:
        clock = clock or DEFAULT_CLOCK
        rid = run_id or str(uuid.uuid4())
        return cls(run_id=rid, run_start_time=clock.now(), mode=mode)

    @property
    def run_id_short(self) -> str:
        return self.run_id.replace("-", "")[:8]

    def add_event(
        self,
        code: str,
        message: str,
        *,
        level: Literal["info", "warning", "error"] = "info",
        **details: Any,
    ) -> None:
        self.events.append(
            RunEvent(code=code, message=message, level=level, details=dict(details))
        )
