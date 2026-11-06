from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from super6.golden_goal.actual import first_goal_minutes, goal_minute, round_golden_goal
from super6.golden_goal.profile import (
    MIN_GOALS_FOR_EMPIRICAL,
    empirical_profile,
    profile_from_config,
    uniform_profile,
)
from super6.golden_goal.simulate import GoldenGoalFixture, simulate_golden_goal
from super6.rules import CompetitionRules

KO = datetime(2026, 11, 7, 15, 0, tzinfo=UTC)


def _goals(rows: list[tuple[str, int, int, float | None, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["fixture_id", "period", "match_minute",
                                       "stoppage_minute", "team_side"])


def _synthetic_goals(n: int, seed: int = 1) -> pd.DataFrame:
    """Goals weighted towards late minutes, with plenty of stoppage-time goals."""
    rng = np.random.default_rng(seed)
    rows: list[tuple[str, int, int, float | None, str]] = []
    for i in range(n):
        u = rng.random()
        if u < 0.05:
            rows.append((f"m{i}", 1, 45, float(rng.integers(1, 4)), "home"))
        elif u < 0.15:
            rows.append((f"m{i}", 2, 90, float(rng.integers(1, 8)), "away"))
        else:
            minute = int(min(90, 1 + rng.triangular(0, 90, 90)))
            rows.append((f"m{i}", 1 if minute <= 45 else 2, minute, None, "home"))
    return _goals(rows)


def test_uniform_profile_cells_and_recorded_minutes() -> None:
    prof = uniform_profile(2, 5)
    assert prof.n_cells == 97 and prof.weights.sum() == pytest.approx(1.0)
    capped = prof.recorded_minutes("capped_at_period_end")
    assert (capped == 45).sum() == 3 and (capped == 90).sum() == 6
    assert capped[0] == 1 and capped[47] == 46
    clock = prof.recorded_minutes("continuous_clock")
    assert list(clock[45:47]) == [46, 47] and clock[-1] == 95
    assert prof.is_second_half().sum() == 50
    cells, p_none = prof.first_goal_cells(2.6)
    assert p_none == pytest.approx(np.exp(-2.6))
    assert cells.sum() + p_none == pytest.approx(1.0)
    mins, p0 = prof.first_goal_minutes(2.6)
    assert mins[0] == 0 and mins.sum() + p0 == pytest.approx(1.0)


def test_empirical_profile_follows_the_goals() -> None:
    goals = _synthetic_goals(2000)
    prof = empirical_profile(goals)
    assert prof.method == "empirical-hazard" and prof.n_goals == 2000
    assert prof.weights.sum() == pytest.approx(1.0)
    stoppage = (goals["stoppage_minute"].notna()).mean()
    s1, s2 = prof.first_half_stoppage, prof.second_half_stoppage
    w = prof.weights
    assert w[45:45 + s1].sum() + w[-s2:].sum() == pytest.approx(stoppage)
    # Goals are weighted late, so the last regular band outweighs the first.
    assert w[85:90 + s1].sum() > w[0:5].sum()
    with pytest.raises(ValueError):
        empirical_profile(goals.head(MIN_GOALS_FOR_EMPIRICAL - 1))


def test_profile_from_config_falls_back_with_a_warning() -> None:
    prof, warnings = profile_from_config({"profile": "empirical"}, _synthetic_goals(50))
    assert prof.method == "uniform-hazard" and warnings[0].startswith(
        "GOLDEN_GOAL_PROFILE_FALLBACK")
    prof, warnings = profile_from_config({"profile": "empirical"}, _synthetic_goals(800))
    assert prof.method == "empirical-hazard" and warnings == []
    assert profile_from_config({}, _synthetic_goals(800))[0].method == "uniform-hazard"
    with pytest.raises(ValueError):
        profile_from_config({"profile": "nonsense"}, _synthetic_goals(800))


def test_exact_earliest_minute_matches_monte_carlo(rules: CompetitionRules) -> None:
    """With equal kick-offs the real-time and earliest-minute readings coincide."""
    prof = empirical_profile(_synthetic_goals(3000))
    fixtures = [GoldenGoalFixture(f"f{i}", KO, lam) for i, lam in
                enumerate([2.1, 2.9, 3.4, 1.8, 2.5, 2.7])]
    exact = simulate_golden_goal(fixtures, rules, profile=prof)
    mc = simulate_golden_goal(
        fixtures, rules.model_copy(update={"golden_goal_first_goal": "first_in_real_time"}),
        n_simulations=60_000, seed=3, profile=prof)
    assert exact.n_simulations == 0 and mc.n_simulations == 60_000
    keys = set(exact.distribution) | set(mc.distribution)
    tv = 0.5 * sum(abs(exact.distribution.get(k, 0) - mc.distribution.get(k, 0)) for k in keys)
    assert tv < 0.02
    assert abs(exact.minute - mc.minute) <= 1
    assert sum(exact.first_goal_fixture_share.values()) == pytest.approx(1.0)
    # Higher expected goals → more often the first to score.
    shares = exact.first_goal_fixture_share
    assert shares["f2"] > shares["f3"]


def test_staggered_kickoffs_change_the_real_time_answer(rules: CompetitionRules) -> None:
    prof = uniform_profile()
    early = [GoldenGoalFixture("early", KO - timedelta(hours=3), 1.0)]
    late = [GoldenGoalFixture(f"late{i}", KO, 3.0) for i in range(5)]
    real = simulate_golden_goal(
        early + late, rules.model_copy(update={"golden_goal_first_goal": "first_in_real_time"}),
        n_simulations=20_000, seed=1, profile=prof)
    # The early game usually scores before the others kick off.
    assert real.first_goal_fixture_share["early"] > 0.5
    earliest = simulate_golden_goal(early + late, rules, profile=prof)
    assert earliest.minute < real.minute


@pytest.mark.parametrize(
    ("period", "minute", "stoppage", "mode", "expected"),
    [(1, 1, None, "capped_at_period_end", 1),
     (1, 45, None, "capped_at_period_end", 45),
     (1, 45, 2, "capped_at_period_end", 45),
     (1, 45, 2, "continuous_clock", 47),
     (2, 46, float("nan"), "capped_at_period_end", 46),
     (2, 90, 4, "capped_at_period_end", 90),
     (2, 90, 4, "continuous_clock", 94)])
def test_goal_minutes_follow_the_rules(period: int, minute: int, stoppage: float | None,
                                       mode: str, expected: int) -> None:
    assert goal_minute(period, minute, stoppage, mode) == expected


def test_first_goal_minutes_need_full_coverage() -> None:
    goals = _goals([
        ("a", 2, 67, None, "home"), ("a", 1, 45, 3.0, "away"),  # 1-1, first goal at 45
        ("b", 1, 12, None, "home"),  # listed 1-0 but the score is 2-0 → uncovered
        ("d", 1, 30, None, "away"),  # a goal listed for a 0-0 match → uncovered
    ])
    scores = {"a": (1, 1), "b": (2, 0), "c": (0, 0), "d": (0, 0), "e": (1, 0)}
    first = first_goal_minutes(goals, scores)
    assert first.minutes == {"a": 45, "c": None}
    assert sorted(first.uncovered) == ["b", "d", "e"]
    assert round_golden_goal(first.minutes, 0) == 45
    assert round_golden_goal({"c": None}, 0) == 0
