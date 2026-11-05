"""Golden Goal minute conventions and chronology (DESIGN.md §3.4, §12).

Minutes are 1-indexed: a goal scored 05:00–05:59 into the first half is minute 6.
Second-half minutes continue from 46. How stoppage-time goals are recorded comes from the
rules config: ``capped_at_period_end`` records 45+2 as 45 and 90+3 as 90;
``continuous_clock`` keeps counting (45+2 → 47 in the first half).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

PERIOD_MINUTES = 45
StoppageMode = str  # "capped_at_period_end" | "continuous_clock"


def recorded_minute(period: int, seconds_into_period: float,
                    stoppage: StoppageMode = "capped_at_period_end") -> int:
    if period not in (1, 2):
        raise ValueError("period must be 1 or 2 (extra time is not part of full-time results)")
    if seconds_into_period < 0:
        raise ValueError("seconds_into_period must be non-negative")
    offset = 0 if period == 1 else PERIOD_MINUTES
    if seconds_into_period >= PERIOD_MINUTES * 60:
        if stoppage == "capped_at_period_end":
            return offset + PERIOD_MINUTES
        if stoppage != "continuous_clock":
            raise ValueError(f"unknown stoppage-time convention {stoppage!r}")
    return offset + math.floor(seconds_into_period / 60) + 1


@dataclass(frozen=True)
class GoalEvent:
    fixture_id: str
    kickoff_utc: datetime
    period: int
    seconds_into_period: float
    first_half_length_seconds: float = 47 * 60  # including stoppage
    half_time_seconds: float = 15 * 60

    @property
    def real_time_utc(self) -> datetime:
        elapsed = self.seconds_into_period
        if self.period == 2:
            elapsed += self.first_half_length_seconds + self.half_time_seconds
        return self.kickoff_utc + timedelta(seconds=elapsed)

    def minute(self, stoppage: StoppageMode = "capped_at_period_end") -> int:
        return recorded_minute(self.period, self.seconds_into_period, stoppage)


def first_goal(events: Sequence[GoalEvent]) -> GoalEvent | None:
    """Earliest goal in real (wall-clock) time across fixtures with staggered kick-offs."""
    if not events:
        return None
    return min(events, key=lambda e: (e.real_time_utc, e.minute(), e.fixture_id))
