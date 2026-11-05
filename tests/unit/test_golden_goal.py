from datetime import UTC, datetime

import numpy as np
import pytest

from super6.golden_goal.minutes import GoalEvent, first_goal, recorded_minute
from super6.golden_goal.simulate import GoldenGoalFixture, simulate_golden_goal
from super6.rules import CompetitionRules

KO = datetime(2026, 11, 7, 15, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("period", "seconds", "expected"),
    [
        (1, 0, 1),
        (1, 59.9, 1),
        (1, 300, 6),  # 05:00 → minute 6
        (1, 359, 6),  # 05:59 → minute 6
        (1, 360, 7),
        (1, 44 * 60 + 59, 45),
        (2, 0, 46),
        (2, 44 * 60 + 30, 90),
    ],
)
def test_one_indexed_minutes(period: int, seconds: float, expected: int) -> None:
    assert recorded_minute(period, seconds) == expected


def test_stoppage_time_conventions() -> None:
    assert recorded_minute(1, 46 * 60 + 10, "capped_at_period_end") == 45
    assert recorded_minute(1, 46 * 60 + 10, "continuous_clock") == 47
    assert recorded_minute(2, 47 * 60, "capped_at_period_end") == 90
    with pytest.raises(ValueError):
        recorded_minute(3, 10)


def test_first_goal_uses_wall_clock_across_staggered_kickoffs() -> None:
    early = datetime(2026, 11, 7, 12, 30, tzinfo=UTC)
    # 12:30 game scores in the 80th minute (≈14:07 wall clock);
    # 15:00 game scores in minute 2 (15:01): the 12:30 goal is the first goal.
    late_goal_early_game = GoalEvent("early", early, 2, 34 * 60)
    early_goal_late_game = GoalEvent("late", KO, 1, 61)
    first = first_goal([early_goal_late_game, late_goal_early_game])
    assert first is late_goal_early_game
    assert first.minute() == 80
    # Under the match-clock definition the minute-2 goal counts instead.
    by_clock = first_goal([early_goal_late_game, late_goal_early_game], "earliest_match_minute")
    assert by_clock is early_goal_late_game and by_clock.minute() == 2
    assert first_goal([]) is None


def test_simulation_median_and_no_goal(rules: CompetitionRules) -> None:
    fixtures = [GoldenGoalFixture(f"f{i}", KO, 2.7) for i in range(6)]
    fc = simulate_golden_goal(fixtures, rules, n_simulations=40_000, seed=1)
    # Six fixtures × 2.7 goals over ~97 playing minutes → combined rate ≈ 0.167/min,
    # so the median first goal time ≈ ln 2 / 0.167 ≈ 4.2 minutes → recorded minute 5.
    assert fc.minute in (4, 5)
    assert sum(fc.distribution.values()) == pytest.approx(1.0)
    assert fc.p_no_goal < 1e-4
    assert fc.interval_low <= fc.minute <= fc.interval_high
    assert set(fc.first_goal_fixture_share) == {f"f{i}" for i in range(6)}
    assert any("verified" in a for a in fc.assumptions)


def test_goalless_round_counts_as_minute_zero(rules: CompetitionRules) -> None:
    # Two near-goalless fixtures: most simulated rounds have no goal, so the rules'
    # answer of 0 becomes the best pick.
    fixtures = [GoldenGoalFixture(f"f{i}", KO, 0.2) for i in range(2)]
    fc = simulate_golden_goal(fixtures, rules, n_simulations=20_000, seed=4)
    assert fc.p_no_goal > 0.6
    assert fc.minute == 0 and fc.distribution[0] == pytest.approx(fc.p_no_goal)
    undefined = rules.model_copy(update={"golden_goal_no_goal_minute": None})
    fc2 = simulate_golden_goal(fixtures, undefined, n_simulations=20_000, seed=4)
    assert 0 not in fc2.distribution and fc2.minute > 0


def test_simulation_respects_kickoff_schedule(rules: CompetitionRules) -> None:
    early = datetime(2026, 11, 7, 12, 30, tzinfo=UTC)
    fixtures = [GoldenGoalFixture("early", early, 2.6)] + [
        GoldenGoalFixture(f"late{i}", KO, 2.6) for i in range(5)]
    real = rules.model_copy(update={"golden_goal_first_goal": "first_in_real_time"})
    fc = simulate_golden_goal(fixtures, real, n_simulations=20_000, seed=2)
    assert fc.first_goal_fixture_share["early"] > 0.85
    # A lone early game has a much later expected first goal than six concurrent games.
    assert fc.minute > 15


def test_match_clock_definition_ignores_kickoff_order(rules: CompetitionRules) -> None:
    early = datetime(2026, 11, 7, 12, 30, tzinfo=UTC)
    fixtures = [GoldenGoalFixture("early", early, 2.6)] + [
        GoldenGoalFixture(f"late{i}", KO, 2.6) for i in range(5)]
    clock = rules.model_copy(update={"golden_goal_first_goal": "earliest_match_minute"})
    real = simulate_golden_goal(
        fixtures, rules.model_copy(update={"golden_goal_first_goal": "first_in_real_time"}),
        n_simulations=20_000, seed=2)
    by_clock = simulate_golden_goal(fixtures, clock, n_simulations=20_000, seed=2)
    # With six games competing on the match clock, the lowest minute is much earlier and
    # the early kick-off no longer dominates.
    assert by_clock.minute < real.minute
    assert by_clock.first_goal_fixture_share["early"] < 0.3
    assert any("earliest_match_minute" in a for a in by_clock.assumptions)


def test_simulation_is_deterministic(rules: CompetitionRules) -> None:
    fixtures = [GoldenGoalFixture(f"f{i}", KO, 2.0 + i / 10) for i in range(6)]
    a = simulate_golden_goal(fixtures, rules, n_simulations=5_000, seed=9)
    b = simulate_golden_goal(fixtures, rules, n_simulations=5_000, seed=9)
    assert a.distribution == b.distribution and a.minute == b.minute
    assert np.isclose(a.expected_abs_error, b.expected_abs_error)


def test_exact_minute_rule_uses_mode(rules: CompetitionRules) -> None:
    exact = rules.model_copy(update={"golden_goal_tiebreak": "exact_minute"})
    fixtures = [GoldenGoalFixture(f"f{i}", KO, 2.7) for i in range(6)]
    fc = simulate_golden_goal(fixtures, exact, n_simulations=20_000, seed=3)
    assert fc.minute == max(fc.distribution, key=lambda m: (fc.distribution[m], -m))
