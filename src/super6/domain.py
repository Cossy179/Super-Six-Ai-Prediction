"""Small domain types shared across the engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Result(StrEnum):
    HOME = "H"
    DRAW = "D"
    AWAY = "A"


def result_of(home_goals: int, away_goals: int) -> Result:
    if home_goals > away_goals:
        return Result.HOME
    if home_goals < away_goals:
        return Result.AWAY
    return Result.DRAW


class FixtureStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    FINISHED = "FINISHED"
    POSTPONED = "POSTPONED"
    ABANDONED = "ABANDONED"
    VOID = "VOID"


@dataclass(frozen=True)
class FixtureSpec:
    """A fixture to be forecast. Team IDs are canonical."""

    fixture_id: str
    competition: str
    home_team: str
    away_team: str
    kickoff_utc: datetime
    neutral: bool = False

    def __post_init__(self) -> None:
        if self.home_team == self.away_team:
            raise ValueError(f"{self.fixture_id}: a team cannot play itself")
        if self.kickoff_utc.tzinfo is None:
            raise ValueError(f"{self.fixture_id}: kickoff must be timezone-aware")


@dataclass(frozen=True)
class MarketOdds:
    """Decimal odds observed at ``available_at`` for one fixture."""

    home: float
    draw: float
    away: float
    available_at: datetime
    over25: float | None = None
    under25: float | None = None
    source: str = "unknown"
    odds_type: str = "prematch"

    def __post_init__(self) -> None:
        for name in ("home", "draw", "away", "over25", "under25"):
            value = getattr(self, name)
            if value is not None and not value > 1.0:
                raise ValueError(f"decimal odds must be > 1.0 (got {name}={value})")
        if self.available_at.tzinfo is None:
            raise ValueError("odds available_at must be timezone-aware")
