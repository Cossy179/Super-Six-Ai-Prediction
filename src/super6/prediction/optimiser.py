"""Competition-specific score selection (DESIGN.md §3, §11).

Given a calibrated exact-score distribution per fixture, choose:

* **jackpot** picks: maximise the probability of the exact score (under independence this
  also maximises the probability that all six are exact);
* **points** picks: maximise expected Super 6 points,
  ``E[pts | s] = exact * p_s + result_only * (p_R - p_s) + incorrect * (1 - p_R)``,
  which with the 5/2/0 table reduces to ``3 p_s + 2 p_R``.

Ties are broken deterministically: higher exact-score probability, then fewer total goals,
then fewer home goals.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np

from super6.domain import result_of
from super6.prediction.score_grid import ScoreDistribution
from super6.rules import CompetitionRules

TIE_BREAK_RULE = "objective desc, exact-score probability desc, total goals asc, home goals asc"
_ROUND = 12  # decimal places used when comparing floating-point objective values


class Objective(StrEnum):
    JACKPOT = "jackpot"
    POINTS = "points"


@dataclass(frozen=True)
class Pick:
    home_goals: int
    away_goals: int
    exact_probability: float
    result_probability: float
    expected_points: float
    objective_value: float

    @property
    def score(self) -> str:
        return f"{self.home_goals}-{self.away_goals}"


@dataclass(frozen=True)
class FixturePicks:
    fixture_id: str
    objective: Objective
    pick: Pick
    alternatives: tuple[Pick, ...]


@dataclass(frozen=True)
class RoundPicks:
    objective: Objective
    fixtures: tuple[FixturePicks, ...]
    expected_points: float
    joint_exact_probability: float
    assumptions: tuple[str, ...] = field(
        default=(
            "Joint probability multiplies per-fixture probabilities, i.e. assumes match "
            "scores are independent and ignores parameter uncertainty.",
        )
    )


def expected_points(
    dist: ScoreDistribution, home_goals: int, away_goals: int, rules: CompetitionRules
) -> float:
    p_exact = dist.prob(home_goals, away_goals)
    p_result = dist.result_probability(result_of(home_goals, away_goals))
    return (
        rules.points_exact_score * p_exact
        + rules.points_correct_result_only * (p_result - p_exact)
        + rules.points_incorrect * (1.0 - p_result)
    )


def rank_scores(
    dist: ScoreDistribution, objective: Objective, rules: CompetitionRules, top: int
) -> list[Pick]:
    """The ``top`` best scorelines for ``objective`` in deterministic tie-break order."""
    g = dist.grid
    h_idx, a_idx = np.indices(g.shape)
    outcomes = dist.outcome_probabilities()
    sign = np.sign(h_idx - a_idx)
    p_result = np.where(sign > 0, outcomes.home, np.where(sign < 0, outcomes.away, outcomes.draw))
    exp_pts = (
        rules.points_exact_score * g
        + rules.points_correct_result_only * (p_result - g)
        + rules.points_incorrect * (1.0 - p_result)
    )
    value = g if Objective(objective) is Objective.JACKPOT else exp_pts
    h, a = h_idx.ravel(), a_idx.ravel()
    order = np.lexsort((
        h,
        h + a,
        -np.round(g.ravel(), _ROUND),
        -np.round(value.ravel(), _ROUND),
    ))
    return [
        Pick(int(h[i]), int(a[i]), float(g.flat[i]), float(p_result.flat[i]),
             float(exp_pts.flat[i]), float(value.flat[i]))
        for i in order[:top]
    ]


def choose_score(
    dist: ScoreDistribution,
    objective: Objective,
    rules: CompetitionRules,
    n_alternatives: int = 3,
) -> tuple[Pick, tuple[Pick, ...]]:
    ranked = rank_scores(dist, Objective(objective), rules, 1 + n_alternatives)
    return ranked[0], tuple(ranked[1:])


def choose_round(
    distributions: Mapping[str, ScoreDistribution],
    objective: Objective,
    rules: CompetitionRules,
    n_alternatives: int = 3,
) -> RoundPicks:
    fixtures: list[FixturePicks] = []
    total_points = 0.0
    joint = 1.0
    for fixture_id, dist in distributions.items():
        pick, alts = choose_score(dist, objective, rules, n_alternatives)
        fixtures.append(FixturePicks(fixture_id, Objective(objective), pick, alts))
        total_points += pick.expected_points
        joint *= pick.exact_probability
    return RoundPicks(Objective(objective), tuple(fixtures), total_points, joint)


def super6_points(
    pred_home: int, pred_away: int, actual_home: int, actual_away: int, rules: CompetitionRules
) -> int:
    if (pred_home, pred_away) == (actual_home, actual_away):
        return rules.points_exact_score
    if result_of(pred_home, pred_away) == result_of(actual_home, actual_away):
        return rules.points_correct_result_only
    return rules.points_incorrect
