"""The actual Golden Goal answer, worked out from stored goal minutes.

Used to settle rounds automatically and to score the Golden Goal model. Only the
``earliest_match_minute`` reading can be settled this way. The real-time reading needs
the wall-clock time of each goal, which the goal source does not give.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

from super6.golden_goal.minutes import PERIOD_MINUTES, recorded_minute


def goal_minute(period: int, match_minute: int, stoppage_minute: float | None,
                stoppage_mode: str = "capped_at_period_end") -> int:
    """The recorded minute of a goal listed as e.g. ``67'`` or ``45+2'``.

    Converted to time into the period (the middle of the listed minute) and recorded by
    ``minutes.recorded_minute``, so the 1-indexed and stoppage-time conventions live in
    one place. A missing stoppage minute may be NaN.
    """
    if period not in (1, 2):
        raise ValueError("period must be 1 or 2")
    offset = 0 if period == 1 else PERIOD_MINUTES
    if stoppage_minute is None or math.isnan(stoppage_minute):
        listed = int(match_minute) - offset
    else:
        listed = PERIOD_MINUTES + int(stoppage_minute)
    return recorded_minute(period, (listed - 1) * 60 + 30, stoppage_mode)


@dataclass(frozen=True)
class FirstGoals:
    """Each fixture's first recorded goal minute (``None`` for a goalless match)."""

    minutes: dict[str, int | None]
    uncovered: list[str]  # scored, but goal minutes missing or not matching the score


def first_goal_minutes(goals: pd.DataFrame, scores: Mapping[str, tuple[int, int]],
                       stoppage_mode: str = "capped_at_period_end") -> FirstGoals:
    """First recorded goal minute per fixture, from goal events and final scores.

    A fixture is covered only when it ended 0-0, or its goal events match its score
    exactly (count per side). Otherwise it is listed as uncovered.
    """
    by_fixture = {str(k): g for k, g in goals.groupby("fixture_id")} if len(goals) else {}
    minutes: dict[str, int | None] = {}
    uncovered: list[str] = []
    for fid, (hg, ag) in scores.items():
        g = by_fixture.get(fid)
        if hg + ag == 0:
            if g is not None and len(g):
                uncovered.append(fid)
            else:
                minutes[fid] = None
            continue
        if g is None or int((g["team_side"] == "home").sum()) != hg or int(
                (g["team_side"] == "away").sum()) != ag:
            uncovered.append(fid)
            continue
        minutes[fid] = min(
            goal_minute(int(period), int(minute),
                            None if stoppage is None else float(stoppage), stoppage_mode)
            for period, minute, stoppage in zip(
                g["period"].tolist(), g["match_minute"].tolist(),
                g["stoppage_minute"].tolist(), strict=True))
    return FirstGoals(minutes, uncovered)


def round_golden_goal(first: Mapping[str, int | None], no_goal_minute: int | None
                      ) -> int | None:
    """Lowest first-goal minute across the round; the rules' no-goal answer if none."""
    scored = [m for m in first.values() if m is not None]
    return min(scored) if scored else no_goal_minute
