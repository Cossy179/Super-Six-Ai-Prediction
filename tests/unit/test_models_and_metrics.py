from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from super6.domain import FixtureSpec, MarketOdds, Result
from super6.evaluation.metrics import (
    calibration_table,
    exact_log_loss,
    outcome_brier,
    paired_bootstrap,
    ranked_probability_score,
)
from super6.features.point_in_time import MatchHistory
from super6.models import MODEL_CLASSES, build_model
from super6.models.base import CutoffAfterKickoffError
from super6.models.market import implied_rates
from super6.models.poisson import _Objective, build_training_data
from super6.prediction.score_grid import OutcomeProbabilities, ScoreDistribution
from super6.synthetic import SyntheticTruth, synthetic_history


def _cutoff(history: MatchHistory) -> pd.Timestamp:
    return history.matches["kickoff_utc"].max() + pd.Timedelta(days=1)


def _fixture(home: str, away: str, cutoff: pd.Timestamp) -> FixtureSpec:
    return FixtureSpec(f"SYN-x-{home}-{away}", "SYN", home, away,
                       (cutoff + pd.Timedelta(days=1)).to_pydatetime())


@pytest.mark.parametrize("dc", [False, True])
def test_analytic_gradient_matches_numeric(dc: bool) -> None:
    from scipy.optimize import approx_fprime

    history, _ = synthetic_history(n_teams=8, seasons=1, seed=4)
    view = history.view(_cutoff(history))
    data = build_training_data(view.results, view.cutoff, 0.002, 1095)
    obj = _Objective(data, 1.5, dc)
    theta = np.random.default_rng(1).normal(0, 0.1, 2 * data.n_teams + 1 + data.n_comps + dc)
    if dc:
        theta[-1] = -0.05
    numeric = approx_fprime(theta, lambda x: obj(x)[0], 1e-7)
    assert np.abs(numeric - obj(theta)[1]).max() < 1e-5


def test_dixon_coles_recovers_known_strengths() -> None:
    history, truth = synthetic_history(n_teams=16, seasons=5, seed=3, rho=-0.1)
    fitted = build_model("dixon_coles", {"xi_per_day": 0.0, "ridge": 0.1}).fit(
        history.view(_cutoff(history)))
    teams = list(truth.attack)
    est = np.array([fitted.attack[t] for t in teams])  # type: ignore[attr-defined]
    true = np.array([truth.attack[t] for t in teams])
    assert np.corrcoef(est, true)[0, 1] > 0.9
    assert fitted.home_advantage["SYN"] == pytest.approx(truth.home, abs=0.08)  # type: ignore[attr-defined]
    assert -0.25 < fitted.rho < 0.05  # type: ignore[attr-defined]


@pytest.mark.parametrize("name", sorted(MODEL_CLASSES))
def test_every_model_returns_valid_distributions(
    name: str, league: tuple[MatchHistory, SyntheticTruth]
) -> None:
    history, _ = league
    cut = history.matches["kickoff_utc"].iloc[-30] - pd.Timedelta(minutes=1)
    view = history.view(cut)
    fitted = build_model(name).fit(view)
    upcoming = history.matches.loc[history.matches["kickoff_utc"] > cut].head(6)
    for r in upcoming.itertuples():
        fx = FixtureSpec(r.fixture_id, r.competition, r.home_team, r.away_team,
                         r.kickoff_utc.to_pydatetime())
        pred = fitted.predict(fx, view)
        problems = pred.distribution.invariant_violations()
        if name == "empirical_score":
            # Declared "other scores" bucket: a deliberately non-negligible tail.
            problems = [p for p in problems if not p.startswith("TAIL_ABOVE_TOLERANCE")]
        assert problems == []
        assert pred.lam_home > 0 and pred.lam_away > 0
        if name == "market_poisson":
            assert "MARKET_ODDS_UNAVAILABLE_FALLBACK" not in pred.warnings


def test_model_refuses_fixture_that_already_kicked_off(
    league: tuple[MatchHistory, SyntheticTruth]
) -> None:
    history, _ = league
    view = history.view(_cutoff(history))
    fitted = build_model("poisson").fit(view)
    past = FixtureSpec("old", "SYN", "team-00", "team-01",
                       (view.cutoff - pd.Timedelta(hours=1)).to_pydatetime())
    with pytest.raises(CutoffAfterKickoffError):
        fitted.predict(past, view)


def test_unknown_team_is_flagged(league: tuple[MatchHistory, SyntheticTruth]) -> None:
    history, _ = league
    view = history.view(_cutoff(history))
    pred = build_model("dixon_coles").fit(view).predict(
        _fixture("team-00", "newcomers", view.cutoff), view)
    assert "UNKNOWN_TEAM:newcomers" in pred.warnings


def test_market_rates_recover_poisson_truth() -> None:
    dist = ScoreDistribution.independent_poisson(1.8, 0.9)
    out = dist.outcome_probabilities()
    g = dist.grid
    total = np.add.outer(np.arange(dist.size), np.arange(dist.size))
    p_over = 1 - g[total <= 2].sum()
    margin = 1.06
    odds = MarketOdds(home=1 / (out.home * margin), draw=1 / (out.draw * margin),
                      away=1 / (out.away * margin), over25=1 / (p_over * margin),
                      under25=1 / ((1 - p_over) * margin),
                      available_at=datetime(2024, 1, 1, tzinfo=UTC))
    lam_h, lam_a, rmse = implied_rates(odds)
    assert lam_h == pytest.approx(1.8, abs=0.01) and lam_a == pytest.approx(0.9, abs=0.01)
    assert rmse < 1e-6


def test_metrics_known_values() -> None:
    certain_home = OutcomeProbabilities(1.0, 0.0, 0.0)
    assert ranked_probability_score(certain_home, Result.HOME) == 0.0
    assert ranked_probability_score(certain_home, Result.AWAY) == pytest.approx(1.0)
    assert outcome_brier(certain_home, Result.AWAY) == pytest.approx(2.0)
    uniform = OutcomeProbabilities(1 / 3, 1 / 3, 1 / 3)
    assert ranked_probability_score(uniform, Result.DRAW) == pytest.approx(1 / 9)
    dist = ScoreDistribution.independent_poisson(1.3, 1.1)
    assert exact_log_loss(dist, 1, 1) == pytest.approx(-np.log(dist.prob(1, 1)))


def test_calibration_and_bootstrap() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 4000)
    hits = rng.uniform(0, 1, 4000) < p
    table = calibration_table(p, hits, bins=(0, 0.5, 1.0))
    assert np.allclose(table["mean_forecast"], table["observed"], atol=0.03)
    diff = pd.Series(rng.normal(0.1, 1, 3000))
    groups = pd.Series(np.repeat(np.arange(300), 10))
    mean, lo, hi = paired_bootstrap(diff, groups, resamples=500, seed=1)
    assert lo < mean < hi and lo > 0


def test_history_rejects_finished_rows_without_goals() -> None:
    history, _ = synthetic_history(n_teams=4, seasons=1)
    bad = history.matches.copy()
    bad.loc[0, "home_goals"] = np.nan
    with pytest.raises(ValueError):
        MatchHistory(bad)


def test_fixture_spec_validation() -> None:
    with pytest.raises(ValueError):
        FixtureSpec("x", "E0", "a", "a", datetime(2024, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError):
        FixtureSpec("x", "E0", "a", "b", datetime(2024, 1, 1) + timedelta(0))


def test_market_dc_matches_the_draw_price_that_poisson_misses() -> None:
    from super6.models.market import _dc_probs, _model_probs, devig, implied_dc

    odds = MarketOdds(home=2.05, draw=3.4, away=3.7, over25=1.95, under25=1.9,
                      available_at=datetime(2024, 1, 1, tzinfo=UTC))
    (ph, pdraw, pa), po = devig(odds)
    lam_h, lam_a, _ = implied_rates(odds)
    assert _model_probs(lam_h, lam_a)[1] < pdraw - 0.01  # Poisson under-prices the draw
    dh, da, rho, rmse = implied_dc(odds)
    assert rho < 0 and rmse < 1e-6
    assert np.allclose(_dc_probs(dh, da, rho), [ph, pdraw, pa, po], atol=1e-6)
    # Without over/under odds, rho is unidentified and stays 0.
    no_ou = MarketOdds(home=2.05, draw=3.4, away=3.7,
                       available_at=datetime(2024, 1, 1, tzinfo=UTC))
    assert implied_dc(no_ou)[2] == 0.0


def test_ensemble_can_use_the_dc_market(league: tuple[MatchHistory, SyntheticTruth]) -> None:
    history, _ = league
    m = history.matches
    cutoff = m["kickoff_utc"].iloc[-12] - pd.Timedelta(minutes=1)
    view = history.view(cutoff)
    r = m.loc[m["kickoff_utc"] > cutoff].iloc[0]
    fx = FixtureSpec(r["fixture_id"], r["competition"], r["home_team"], r["away_team"],
                     r["kickoff_utc"].to_pydatetime())
    ens = build_model("ensemble", {"market_model": "market_dc"}).fit(view).predict(fx, view)
    direct = build_model("market_dc").fit(view).predict(fx, view)
    assert np.array_equal(ens.distribution.grid, direct.distribution.grid)
