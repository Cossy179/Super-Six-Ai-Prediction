"""Golden Goal baseline: Monte Carlo over first-goal times (DESIGN.md §12).

**This is a labelled baseline, not a trained event-time model.** No licensed goal-timing
data is ingested yet, so each fixture's goals are assumed to arrive at a constant rate
(its expected total goals spread evenly over playing time including stoppage). Real
scoring rates are known to vary through a match; that is left for a hazard model once
timing data exist (DESIGN.md §12.3).

Per simulation: draw each fixture's first-goal time, map it to wall-clock time using the
actual kick-off, take the earliest across fixtures, and convert it to the recorded
minute. The recommendation minimises expected absolute error (median) when the
tie-break is closest-minute, or takes the mode for exact-minute rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from super6.rules import CompetitionRules

METHOD = "uniform-hazard-poisson-simulation"


@dataclass(frozen=True)
class GoldenGoalFixture:
    fixture_id: str
    kickoff_utc: datetime
    expected_total_goals: float


@dataclass(frozen=True)
class GoldenGoalForecast:
    minute: int
    method: str
    # recorded minute -> probability. Includes the rules' no-goal answer (e.g. 0) when the
    # rules define one; otherwise conditional on at least one goal.
    distribution: dict[int, float]
    p_no_goal: float
    expected_abs_error: float
    interval_low: int  # 10th percentile
    interval_high: int  # 90th percentile
    n_simulations: int
    seed: int
    first_goal_fixture_share: dict[str, float]
    assumptions: tuple[str, ...] = field(default_factory=tuple)


def _recorded_minutes(t: np.ndarray, first_half: float, stoppage: str) -> np.ndarray:
    """Playing-time minutes since kick-off → recorded 1-indexed minute."""
    in_first = t < first_half
    second = t - first_half
    capped = stoppage == "capped_at_period_end"
    m1 = np.floor(t) + 1
    m2 = 45 + np.floor(second) + 1
    if capped:
        m1 = np.minimum(m1, 45)
        m2 = np.minimum(m2, 90)
    return np.where(in_first, m1, m2).astype(np.int64)


def simulate_golden_goal(
    fixtures: Sequence[GoldenGoalFixture],
    rules: CompetitionRules,
    *,
    n_simulations: int = 50_000,
    seed: int = 20261105,
    first_half_stoppage_minutes: float = 2.0,
    second_half_stoppage_minutes: float = 5.0,
    half_time_break_minutes: float = 15.0,
) -> GoldenGoalForecast:
    if not fixtures:
        raise ValueError("no fixtures to simulate")
    rng = np.random.default_rng(seed)
    first_half = 45.0 + first_half_stoppage_minutes
    playing = first_half + 45.0 + second_half_stoppage_minutes
    epoch = min(f.kickoff_utc for f in fixtures)
    n = n_simulations
    k = len(fixtures)
    wall = np.full((n, k), np.inf)
    minute = np.zeros((n, k), dtype=np.int64)
    for j, fx in enumerate(fixtures):
        if fx.expected_total_goals <= 0:
            continue
        rate = fx.expected_total_goals / playing
        t = rng.exponential(1.0 / rate, size=n)
        scored = t < playing
        offset = (fx.kickoff_utc - epoch).total_seconds() / 60.0
        real = offset + np.where(t < first_half, t, t + half_time_break_minutes)
        wall[:, j] = np.where(scored, real, np.inf)
        minute[:, j] = _recorded_minutes(np.where(scored, t, 0.0), first_half,
                                         rules.golden_goal_stoppage_time)
    if rules.golden_goal_first_goal == "earliest_match_minute":
        # Lowest recorded minute in any match, regardless of kick-off times.
        clock = np.where(np.isfinite(wall), minute.astype(float), np.inf)
        first_idx = np.argmin(clock, axis=1)
    else:
        first_idx = np.argmin(wall, axis=1)
    any_goal = np.isfinite(wall[np.arange(n), first_idx])
    p_no_goal = float(1.0 - any_goal.mean())
    if not any_goal.any() and rules.golden_goal_no_goal_minute is None:
        raise ValueError("no simulated goals; check expected goals inputs")
    first_minutes = minute[np.arange(n), first_idx][any_goal]
    if rules.golden_goal_no_goal_minute is not None:
        # The rules define the answer for a goalless round, so it is part of the outcome
        # distribution the recommendation is optimised over.
        first_minutes = np.concatenate([
            first_minutes,
            np.full(int((~any_goal).sum()), rules.golden_goal_no_goal_minute, dtype=np.int64),
        ])
    values, counts = np.unique(first_minutes, return_counts=True)
    probs = counts / counts.sum()
    distribution = {int(v): float(p) for v, p in zip(values, probs, strict=True)}
    cdf = np.cumsum(probs)
    if rules.golden_goal_tiebreak == "closest_minute":
        choice = int(values[int(np.searchsorted(cdf, 0.5))])  # lower median: min E|error|
    else:
        choice = int(values[np.argmax(probs)])  # mode; argmax returns the earliest tie
    eae = float(np.sum(probs * np.abs(values - choice)))
    lo = int(values[int(np.searchsorted(cdf, 0.10))])
    hi = int(values[min(int(np.searchsorted(cdf, 0.90)), len(values) - 1)])
    share_idx, share_counts = np.unique(first_idx[any_goal], return_counts=True)
    share = {fixtures[int(i)].fixture_id: float(c / any_goal.sum())
             for i, c in zip(share_idx, share_counts, strict=True)}
    assumptions = (
        "No goal-timing data: each fixture's scoring rate is assumed constant over the match.",
        f"Stoppage time fixed at {first_half_stoppage_minutes:g} (1st half) and "
        f"{second_half_stoppage_minutes:g} (2nd half) minutes; half-time "
        f"{half_time_break_minutes:g} minutes.",
        f"Stoppage-time goals recorded as '{rules.golden_goal_stoppage_time}' "
        f"(rules {'verified' if rules.verified else 'UNVERIFIED'}).",
        f"First goal defined as '{rules.golden_goal_first_goal}'.",
        "Fixtures are independent; delayed kick-offs are not modelled.",
    )
    return GoldenGoalForecast(choice, METHOD, distribution, p_no_goal, eae, lo, hi, n, seed,
                              share, assumptions)
