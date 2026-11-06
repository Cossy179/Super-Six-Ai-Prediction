"""When goals happen within a match: goal-time profiles for the Golden Goal model.

A match is split into one-minute *cells* of playing time:

* 45 first-half cells (recorded minutes 1–45);
* ``first_half_stoppage`` cells;
* 45 second-half cells (46–90);
* ``second_half_stoppage`` cells.

A profile gives the share of a match's goals expected in each cell. Goals arrive as a
Poisson process with intensity ``λ_total × weight(cell)``, where ``λ_total`` is the match's
expected goals from the score model. So the first goal falls in cell ``c`` with
probability ``exp(−λ W[c−1]) − exp(−λ W[c])``, where ``W`` is the cumulative weight.

* ``uniform_profile``: every minute equally likely. This is the original baseline.
* ``empirical_profile``: shares by 5-minute band, plus each half's stoppage time, from
  observed goal minutes (openfootball, CC0), using only matches published before the
  cutoff.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
MIN_GOALS_FOR_EMPIRICAL = 400


@dataclass(frozen=True)
class GoalTimeProfile:
    weights: FloatArray  # one per cell, sums to 1
    first_half_stoppage: int
    second_half_stoppage: int
    method: str
    n_goals: int = 0

    def __post_init__(self) -> None:
        expected = 90 + self.first_half_stoppage + self.second_half_stoppage
        if len(self.weights) != expected or abs(float(self.weights.sum()) - 1.0) > 1e-9:
            raise ValueError("profile weights must cover every cell and sum to 1")

    @property
    def n_cells(self) -> int:
        return len(self.weights)

    def recorded_minutes(self, stoppage_mode: str = "capped_at_period_end") -> IntArray:
        """The recorded (1-indexed) minute for a goal in each cell."""
        s1, s2 = self.first_half_stoppage, self.second_half_stoppage
        capped = stoppage_mode == "capped_at_period_end"
        first_stop = [45] * s1 if capped else list(range(46, 46 + s1))
        second_stop = [90] * s2 if capped else list(range(91, 91 + s2))
        return np.array([*range(1, 46), *first_stop, *range(46, 91), *second_stop],
                        dtype=np.int64)

    def playing_minutes(self) -> FloatArray:
        """Elapsed playing time at the middle of each cell (stoppage included)."""
        return np.arange(self.n_cells, dtype=np.float64) + 0.5

    def is_second_half(self) -> NDArray[np.bool_]:
        return np.arange(self.n_cells) >= 45 + self.first_half_stoppage

    def first_goal_cells(self, lam_total: float) -> tuple[FloatArray, float]:
        """P(first goal in each cell) and P(no goal)."""
        cum = np.cumsum(self.weights)
        surv = np.exp(-lam_total * np.concatenate([[0.0], cum]))
        return surv[:-1] - surv[1:], float(surv[-1])

    def first_goal_minutes(self, lam_total: float, stoppage_mode: str = "capped_at_period_end"
                           ) -> tuple[FloatArray, float]:
        """P(first recorded minute = m) for m = 0..max (index 0 unused), and P(no goal)."""
        cells, p_none = self.first_goal_cells(lam_total)
        minutes = self.recorded_minutes(stoppage_mode)
        out = np.zeros(int(minutes.max()) + 1)
        np.add.at(out, minutes, cells)
        return out, p_none


def uniform_profile(first_half_stoppage: int = 2, second_half_stoppage: int = 5
                    ) -> GoalTimeProfile:
    n = 90 + first_half_stoppage + second_half_stoppage
    return GoalTimeProfile(np.full(n, 1.0 / n), first_half_stoppage, second_half_stoppage,
                           "uniform-hazard")


def empirical_profile(goals: pd.DataFrame, bin_minutes: int = 5, first_half_stoppage: int = 3,
                      second_half_stoppage: int = 7) -> GoalTimeProfile:
    """Fit from goal minutes (columns ``match_minute``, ``stoppage_minute``).

    Regular-time goals are counted in ``bin_minutes`` bands within each half and spread
    evenly over each band's minutes. Stoppage-time goals are spread evenly over that half's
    stoppage cells. Only the total stoppage share affects recorded minutes under the
    capped (45/90) rule.
    """
    if len(goals) < MIN_GOALS_FOR_EMPIRICAL:
        raise ValueError(f"need at least {MIN_GOALS_FOR_EMPIRICAL} goals, have {len(goals)}")
    minute = goals["match_minute"].astype(int).to_numpy()
    stoppage = goals["stoppage_minute"].notna().to_numpy()
    regular = np.zeros(90)
    for half_start in (1, 46):
        for band_start in range(half_start, half_start + 45, bin_minutes):
            band = range(band_start, min(band_start + bin_minutes, half_start + 45))
            count = int(np.isin(minute[~stoppage], list(band)).sum())
            for m in band:
                regular[m - 1] = count / len(band)
    first_stop = int(((minute <= 45) & stoppage).sum())
    second_stop = int(((minute > 45) & stoppage).sum())
    weights = np.concatenate([
        regular[:45], np.full(first_half_stoppage, first_stop / first_half_stoppage),
        regular[45:], np.full(second_half_stoppage, second_stop / second_half_stoppage),
    ])
    weights = weights / weights.sum()
    return GoalTimeProfile(weights, first_half_stoppage, second_half_stoppage,
                           "empirical-hazard", n_goals=len(goals))


def profile_from_config(config: Mapping[str, Any], goals: pd.DataFrame
                        ) -> tuple[GoalTimeProfile, list[str]]:
    """The profile a forecast uses, and any warnings.

    ``config["profile"]`` is ``uniform`` (default) or ``empirical``. ``goals`` must already
    be limited to what was public at the forecast cutoff (``PointInTimeView.goal_events``).
    An empirical profile with too few goals falls back to uniform, with a warning.
    """
    s1 = round(float(config.get("first_half_stoppage_minutes", 2.0)))
    s2 = round(float(config.get("second_half_stoppage_minutes", 5.0)))
    kind = str(config.get("profile", "uniform"))
    if kind == "uniform":
        return uniform_profile(s1, s2), []
    if kind != "empirical":
        raise ValueError(f"unknown Golden Goal profile {kind!r}")
    if len(goals) < MIN_GOALS_FOR_EMPIRICAL:
        return uniform_profile(s1, s2), [
            f"GOLDEN_GOAL_PROFILE_FALLBACK: {len(goals)} goal minutes known, "
            f"{MIN_GOALS_FOR_EMPIRICAL} needed; using uniform"]
    return empirical_profile(
        goals, int(config.get("bin_minutes", 5)),
        int(config.get("empirical_first_half_stoppage_cells", 3)),
        int(config.get("empirical_second_half_stoppage_cells", 7))), []
