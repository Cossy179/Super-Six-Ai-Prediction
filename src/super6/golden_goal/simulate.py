"""Golden Goal forecast from per-fixture goal-time profiles (DESIGN.md §12).

Each fixture's first goal follows a goal-time profile (``profile.py``) scaled by its
expected total goals. Two readings of "the first goal" are supported (rules field
``golden_goal_first_goal``):

* ``earliest_match_minute``, the configured reading: the lowest recorded minute in any of
  the six games. The round's distribution is computed exactly, since
  ``P(Golden Goal > m) = Π_j P(fixture j's first recorded minute > m)``.
* ``first_in_real_time``: the first goal by wall clock, which needs kick-off times. It is
  estimated by Monte Carlo: each fixture's first-goal cell is drawn from its profile and
  mapped to wall-clock time.

A goalless round takes the rules' answer (0). The pick minimises expected absolute error
(the lower median) for closest-minute tie-breaks, or is the mode for exact-minute rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from super6.golden_goal.profile import GoalTimeProfile, uniform_profile
from super6.rules import CompetitionRules

METHOD = "uniform-hazard"  # default profile label; empirical profiles carry their own


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
    n_simulations: int  # 0 when computed exactly
    seed: int
    first_goal_fixture_share: dict[str, float]
    assumptions: tuple[str, ...] = field(default_factory=tuple)


def _summarise(values: NDArray[np.int64], probs: NDArray[np.float64], rules: CompetitionRules
               ) -> tuple[int, float, int, int]:
    probs = probs / probs.sum()
    cdf = np.cumsum(probs)
    if rules.golden_goal_tiebreak == "closest_minute":
        choice = int(values[int(np.searchsorted(cdf, 0.5 - 1e-12))])
    else:
        choice = int(values[int(np.argmax(probs))])
    eae = float(np.sum(probs * np.abs(values - choice)))
    lo = int(values[int(np.searchsorted(cdf, 0.10 - 1e-12))])
    hi = int(values[min(int(np.searchsorted(cdf, 0.90 - 1e-12)), len(values) - 1)])
    return choice, eae, lo, hi


def earliest_minute_distribution(
    fixtures: Sequence[GoldenGoalFixture], profile: GoalTimeProfile, stoppage_mode: str
) -> tuple[NDArray[np.float64], float, dict[str, float]]:
    """Exact P(Golden Goal = m) for m = 1..max, P(no goal anywhere), and fixture shares."""
    per_fixture = []
    for fx in fixtures:
        mins, p_none = profile.first_goal_minutes(max(fx.expected_total_goals, 1e-9),
                                                  stoppage_mode)
        # survival[m] = P(first recorded minute > m), with no goal counting as > every m
        survival = p_none + mins[::-1].cumsum()[::-1] - mins
        per_fixture.append((mins, survival, p_none))
    size = max(len(m) for m, _, _ in per_fixture)
    surv_all = np.ones(size)
    for _, survival, _ in per_fixture:
        surv_all *= np.pad(survival, (0, size - len(survival)), constant_values=survival[-1])
    before = np.concatenate([[1.0], surv_all[:-1]])  # P(GG > m-1)
    dist = before - surv_all
    dist[0] = 0.0
    p_none_all = float(np.prod([p for _, _, p in per_fixture]))
    shares = {}
    for j, fx in enumerate(fixtures):
        mins, _, _ = per_fixture[j]
        others = np.ones(size)
        for k, (_, s_k, _) in enumerate(per_fixture):
            if k != j:
                others *= np.pad(s_k, (0, size - len(s_k)), constant_values=s_k[-1])
        # Fixture j scores at m while every other fixture's first goal is later (or never);
        # exact ties are rare and are not split.
        shares[fx.fixture_id] = float(np.sum(np.pad(mins, (0, size - len(mins))) * others))
    total = sum(shares.values()) or 1.0
    return dist, p_none_all, {k: v / total for k, v in shares.items()}


def simulate_golden_goal(
    fixtures: Sequence[GoldenGoalFixture],
    rules: CompetitionRules,
    *,
    n_simulations: int = 50_000,
    seed: int = 20261105,
    first_half_stoppage_minutes: float = 2.0,
    second_half_stoppage_minutes: float = 5.0,
    half_time_break_minutes: float = 15.0,
    profile: GoalTimeProfile | None = None,
) -> GoldenGoalForecast:
    if not fixtures:
        raise ValueError("no fixtures to simulate")
    prof = profile or uniform_profile(round(first_half_stoppage_minutes),
                                      round(second_half_stoppage_minutes))
    mode = rules.golden_goal_stoppage_time
    no_goal_value = rules.golden_goal_no_goal_minute
    if rules.golden_goal_first_goal == "earliest_match_minute":
        dist, p_no_goal, shares = earliest_minute_distribution(fixtures, prof, mode)
        values = np.arange(len(dist), dtype=np.int64)
        probs = dist.copy()
        if no_goal_value is not None:
            probs[no_goal_value] += p_no_goal
        n_used = 0
    else:
        values, probs, p_no_goal, shares = _simulate_real_time(
            fixtures, prof, mode, n_simulations, seed, half_time_break_minutes, no_goal_value)
        n_used = n_simulations
    keep = probs > 0
    if not keep.any():
        raise ValueError("no simulated goals; check expected goals inputs")
    values, probs = values[keep], probs[keep]
    probs = probs / probs.sum()
    choice, eae, lo, hi = _summarise(values, probs, rules)
    distribution = {int(v): float(p) for v, p in zip(values, probs, strict=True)}
    label = prof.method + ("" if n_used == 0 else "-simulation")
    assumptions = (
        ("Goal timing: constant scoring rate through each match (no timing data)."
         if prof.method == "uniform-hazard" else
         f"Goal timing: empirical profile from {prof.n_goals} observed goals "
         "(openfootball, CC0), scaled by each match's expected goals."),
        f"Stoppage time: {prof.first_half_stoppage} and {prof.second_half_stoppage} minutes; "
        f"stoppage goals recorded as '{mode}' (rules "
        f"{'verified' if rules.verified else 'UNVERIFIED'}).",
        f"First goal defined as '{rules.golden_goal_first_goal}'.",
        "Fixtures are independent; delayed kick-offs are not modelled.",
    )
    return GoldenGoalForecast(choice, label, distribution, p_no_goal, eae, lo, hi, n_used,
                              seed, shares, assumptions)


def _simulate_real_time(
    fixtures: Sequence[GoldenGoalFixture], prof: GoalTimeProfile, mode: str, n: int,
    seed: int, half_time_break: float, no_goal_value: int | None,
) -> tuple[NDArray[np.int64], NDArray[np.float64], float, dict[str, float]]:
    rng = np.random.default_rng(seed)
    epoch = min(f.kickoff_utc for f in fixtures)
    minutes_by_cell = prof.recorded_minutes(mode)
    elapsed = prof.playing_minutes() + np.where(prof.is_second_half(), half_time_break, 0.0)
    k = len(fixtures)
    wall = np.full((n, k), np.inf)
    minute = np.zeros((n, k), dtype=np.int64)
    for j, fx in enumerate(fixtures):
        if fx.expected_total_goals <= 0:
            continue
        cells, _ = prof.first_goal_cells(fx.expected_total_goals)
        cdf = np.cumsum(cells)
        u = rng.random(n)
        scored = u < cdf[-1]
        idx = np.minimum(np.searchsorted(cdf, u), len(cells) - 1)
        offset = (fx.kickoff_utc - epoch).total_seconds() / 60.0
        wall[:, j] = np.where(scored, offset + elapsed[idx], np.inf)
        minute[:, j] = np.where(scored, minutes_by_cell[idx], 0)
    first_idx = np.argmin(wall, axis=1)
    any_goal = np.isfinite(wall[np.arange(n), first_idx])
    p_no_goal = float(1.0 - any_goal.mean())
    first_minutes = minute[np.arange(n), first_idx][any_goal]
    if no_goal_value is not None:
        first_minutes = np.concatenate([
            first_minutes, np.full(int((~any_goal).sum()), no_goal_value, dtype=np.int64)])
    values, counts = np.unique(first_minutes, return_counts=True)
    share_idx, share_counts = np.unique(first_idx[any_goal], return_counts=True)
    shares = {fixtures[int(i)].fixture_id: float(c / max(any_goal.sum(), 1))
              for i, c in zip(share_idx, share_counts, strict=True)}
    return values.astype(np.int64), counts.astype(np.float64), p_no_goal, shares
