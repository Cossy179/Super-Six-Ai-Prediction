import numpy as np
import pytest

from super6.domain import Result
from super6.prediction.score_grid import (
    ProbabilityIntegrityError,
    ScoreDistribution,
    dixon_coles_distribution,
    feasible_rho,
)


@pytest.mark.parametrize(("lam_h", "lam_a"), [(1.5, 1.1), (0.3, 0.2), (3.8, 0.4), (5.5, 4.5)])
def test_poisson_grid_mass_and_tail(lam_h: float, lam_a: float) -> None:
    dist = ScoreDistribution.independent_poisson(lam_h, lam_a, tail_tolerance=1e-6)
    assert dist.grid.min() >= 0
    assert dist.grid.sum() + dist.tail == pytest.approx(1.0, abs=1e-12)
    assert dist.tail <= 1e-6
    assert dist.invariant_violations() == []
    out = dist.outcome_probabilities()
    assert out.home + out.draw + out.away + dist.tail == pytest.approx(1.0, abs=1e-12)


def test_grid_grows_for_high_scoring_matches() -> None:
    small = ScoreDistribution.independent_poisson(1.0, 1.0)
    big = ScoreDistribution.independent_poisson(6.0, 5.0)
    assert big.max_goals > small.max_goals
    assert big.tail <= 1e-6


def test_truncated_grid_reports_tail_instead_of_renormalising() -> None:
    dist = ScoreDistribution.independent_poisson(9.0, 9.0, max_goals=12)
    assert dist.tail > 1e-6
    assert any(p.startswith("TAIL_ABOVE_TOLERANCE") for p in dist.invariant_violations())
    assert dist.grid.sum() < 1.0


def test_rejects_unnormalised_or_negative_grids() -> None:
    with pytest.raises(ProbabilityIntegrityError):
        ScoreDistribution(np.full((3, 3), 0.2), 0.0)
    bad = np.full((2, 2), 0.25)
    bad[0, 0] = -0.25
    bad[1, 1] = 0.75
    with pytest.raises(ProbabilityIntegrityError):
        ScoreDistribution(bad, 0.0)


def test_grid_is_read_only() -> None:
    dist = ScoreDistribution.independent_poisson(1.2, 1.0)
    with pytest.raises(ValueError):
        dist.grid[0, 0] = 1.0


def test_outcomes_match_grid_sums() -> None:
    dist = ScoreDistribution.independent_poisson(1.7, 0.9)
    g = dist.grid
    home = sum(g[h, a] for h in range(dist.size) for a in range(dist.size) if h > a)
    assert dist.result_probability(Result.HOME) == pytest.approx(home)
    assert dist.result_probability(Result.DRAW) == pytest.approx(np.trace(g))


@pytest.mark.parametrize("rho", [-0.15, -0.05, 0.0, 0.08])
def test_dixon_coles_preserves_mass_and_moves_low_scores(rho: float) -> None:
    base = ScoreDistribution.independent_poisson(1.4, 1.1)
    dc, used = dixon_coles_distribution(1.4, 1.1, rho)
    assert used == rho
    assert dc.grid.sum() == pytest.approx(base.grid.sum(), abs=1e-14)
    assert dc.tail == base.tail
    assert np.allclose(dc.grid[2:, :], base.grid[2:, :])
    if rho < 0:  # negative rho inflates draws 0-0 and 1-1
        assert dc.prob(0, 0) > base.prob(0, 0)
        assert dc.prob(1, 1) > base.prob(1, 1)


def test_rho_shrunk_when_tau_would_be_negative() -> None:
    assert feasible_rho(6.0, 1.0, -0.2) == pytest.approx(-1 / 6)
    dist, used = dixon_coles_distribution(6.0, 1.0, -0.2)
    assert used == pytest.approx(-1 / 6)
    assert dist.grid.min() >= 0


def test_log_probability_uses_tail_for_off_grid_scores() -> None:
    dist = ScoreDistribution.independent_poisson(1.0, 1.0, max_goals=10)
    assert dist.log_probability(25, 0) == pytest.approx(np.log(max(dist.tail, 1e-15)))
    assert dist.log_probability(1, 1) == pytest.approx(np.log(dist.prob(1, 1)))


def test_mixture_weights_and_shapes() -> None:
    a = ScoreDistribution.independent_poisson(1.0, 1.0)
    b = ScoreDistribution.independent_poisson(4.0, 3.0)
    mix = ScoreDistribution.mixture([a, b], [0.25, 0.75])
    assert mix.size == max(a.size, b.size)
    assert mix.prob(1, 1) == pytest.approx(0.25 * a.prob(1, 1) + 0.75 * b.prob(1, 1))
    with pytest.raises(ValueError):
        ScoreDistribution.mixture([a, b], [0.5, 0.6])


def test_top_scores_deterministic_ordering() -> None:
    g = np.zeros((3, 3))
    g[1, 0] = g[0, 1] = g[1, 1] = 0.3
    g[0, 0] = 0.1
    top = ScoreDistribution(g, 0.0).top_scores(3)
    assert [(s.home_goals, s.away_goals) for s in top] == [(0, 1), (1, 0), (1, 1)]
