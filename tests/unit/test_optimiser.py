import numpy as np
import pytest

from super6.domain import result_of
from super6.prediction.optimiser import (
    Objective,
    choose_round,
    choose_score,
    expected_points,
    super6_points,
)
from super6.prediction.score_grid import ScoreDistribution
from super6.rules import CompetitionRules


def _point_mass(h: int, a: int, side: int = 4) -> ScoreDistribution:
    g = np.zeros((side, side))
    g[h, a] = 1.0
    return ScoreDistribution(g, 0.0)


def test_points_table(rules: CompetitionRules) -> None:
    assert super6_points(2, 1, 2, 1, rules) == 5  # exact: 5 total, not 5 + 2
    assert super6_points(1, 0, 3, 1, rules) == 2  # correct result only
    assert super6_points(1, 1, 2, 2, rules) == 2
    assert super6_points(0, 1, 1, 0, rules) == 0


def test_expected_points_on_point_masses(rules: CompetitionRules) -> None:
    dist = _point_mass(2, 1)
    assert expected_points(dist, 2, 1, rules) == pytest.approx(5.0)
    assert expected_points(dist, 1, 0, rules) == pytest.approx(2.0)
    assert expected_points(dist, 1, 1, rules) == pytest.approx(0.0)


def test_expected_points_identity(rules: CompetitionRules) -> None:
    dist = ScoreDistribution.independent_poisson(1.6, 1.2)
    for h, a in [(0, 0), (1, 0), (2, 1), (0, 2), (3, 3)]:
        p_s = dist.prob(h, a)
        p_r = dist.result_probability(result_of(h, a))
        assert expected_points(dist, h, a, rules) == pytest.approx(3 * p_s + 2 * p_r)


def test_jackpot_selects_mode_and_points_can_differ(rules: CompetitionRules) -> None:
    # 1-1 is the single most likely score, but home wins have more total probability.
    g = np.zeros((4, 4))
    g[1, 1] = 0.20
    g[1, 0] = 0.18
    g[2, 0] = 0.15
    g[2, 1] = 0.15
    g[0, 1] = 0.12
    g[0, 0] = 0.10
    g[3, 0] = 0.10
    dist = ScoreDistribution(g, 0.0)
    jackpot, _ = choose_score(dist, Objective.JACKPOT, rules)
    points, alts = choose_score(dist, Objective.POINTS, rules)
    assert jackpot.score == "1-1"
    assert points.score == "1-0"  # 3*0.18 + 2*0.58 = 1.70 beats 3*0.20 + 2*0.30 = 1.20
    assert points.expected_points == pytest.approx(1.70)
    assert [p.expected_points for p in alts] == sorted((p.expected_points for p in alts),
                                                      reverse=True)


def test_points_choice_is_brute_force_optimal(rules: CompetitionRules) -> None:
    rng = np.random.default_rng(0)
    for _ in range(25):
        g = rng.dirichlet(np.ones(25)).reshape(5, 5)
        dist = ScoreDistribution(g, 0.0)
        best, _ = choose_score(dist, Objective.POINTS, rules)
        brute = max(expected_points(dist, h, a, rules) for h, a in dist.scorelines())
        assert best.expected_points == pytest.approx(brute)


def test_deterministic_tie_breaking(rules: CompetitionRules) -> None:
    g = np.zeros((3, 3))
    g[1, 0] = g[0, 1] = 0.5
    pick, _ = choose_score(ScoreDistribution(g, 0.0), Objective.JACKPOT, rules)
    assert pick.score == "0-1"  # equal probability and goals → fewer home goals first
    g = np.zeros((3, 3))
    g[2, 0] = g[1, 0] = 0.5
    pick, _ = choose_score(ScoreDistribution(g, 0.0), Objective.JACKPOT, rules)
    assert pick.score == "1-0"  # fewer total goals first


def test_round_joint_probability_and_total(rules: CompetitionRules) -> None:
    dists = {f"f{i}": ScoreDistribution.independent_poisson(1.2 + 0.1 * i, 1.0)
             for i in range(6)}
    rp = choose_round(dists, Objective.JACKPOT, rules)
    assert len(rp.fixtures) == 6
    assert rp.joint_exact_probability == pytest.approx(
        np.prod([f.pick.exact_probability for f in rp.fixtures]))
    assert "independent" in rp.assumptions[0]
    pts = choose_round(dists, Objective.POINTS, rules)
    assert pts.expected_points >= rp.expected_points - 1e-12
