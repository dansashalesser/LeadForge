"""Time as an injected value (requirement 8.6, design D5).

Nothing in the slice reads the system clock directly: state is derived from events and a
``Clock``, so a test advances five days without waiting.
"""

from datetime import UTC, datetime, timedelta
from typing import Protocol

__all__ = ["Clock", "FakeClock", "SystemClock"]


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """A clock that only moves when told to."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("a clock needs an aware datetime")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, *, days: float = 0, hours: float = 0) -> datetime:
        if days < 0 or hours < 0:
            raise ValueError("a clock does not run backwards")
        self._now += timedelta(days=days, hours=hours)
        return self._now
