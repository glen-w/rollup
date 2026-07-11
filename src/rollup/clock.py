"""Injectable clock for deterministic digests and tests."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current aware datetime."""


class SystemClock:
    """Production clock using the local timezone."""

    def now(self) -> datetime:
        return datetime.now().astimezone()


class FixedClock:
    """Test clock that always returns a fixed instant."""

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            instant = instant.astimezone()
        self._instant = instant

    def now(self) -> datetime:
        return self._instant


DEFAULT_CLOCK: Clock = SystemClock()
