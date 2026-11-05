import numpy as np
import pandas as pd
import pytest

from super6.domain import FixtureSpec
from super6.features.point_in_time import MatchHistory
from super6.models import build_model
from super6.models.ensemble import EnsembleModel
from super6.synthetic import SyntheticTruth


def _setup(league: tuple[MatchHistory, SyntheticTruth]) -> tuple[MatchHistory,
                                                                    pd.Timestamp, FixtureSpec]:
    history, _ = league
    m = history.matches
    cutoff = m["kickoff_utc"].iloc[-12] - pd.Timedelta(minutes=1)
    r = m.loc[m["kickoff_utc"] > cutoff].iloc[0]
    fx = FixtureSpec(r["fixture_id"], r["competition"], r["home_team"], r["away_team"],
                     r["kickoff_utc"].to_pydatetime())
    return history, cutoff, fx


@pytest.mark.parametrize("weight", [0.0, 0.3, 1.0])
def test_mixture_matches_components(weight: float,
                                    league: tuple[MatchHistory, SyntheticTruth]) -> None:
    history, cutoff, fx = _setup(league)
    view = history.view(cutoff)
    ens = build_model("ensemble", {"market_weight": weight}).fit(view).predict(fx, view)
    team = build_model("dixon_coles").fit(view).predict(fx, view)
    market = build_model("market_poisson").fit(view).predict(fx, view)
    size = max(team.distribution.size, market.distribution.size)
    expected = np.zeros((size, size))
    expected[: team.distribution.size, : team.distribution.size] += (1 - weight) * \
        team.distribution.grid
    expected[: market.distribution.size, : market.distribution.size] += weight * \
        market.distribution.grid
    if weight == 0.0:
        assert np.array_equal(ens.distribution.grid, team.distribution.grid)
    elif weight == 1.0:
        assert np.array_equal(ens.distribution.grid, market.distribution.grid)
    else:
        assert np.allclose(ens.distribution.grid, expected, atol=1e-15)
    assert ens.lam_home == pytest.approx((1 - weight) * team.lam_home + weight * market.lam_home)
    assert ens.distribution.invariant_violations() == []


def test_falls_back_to_team_model_without_odds(
    league: tuple[MatchHistory, SyntheticTruth]
) -> None:
    history, cutoff, fx = _setup(league)
    no_odds = MatchHistory(history.matches,
                           history.odds.loc[history.odds["fixture_id"] != fx.fixture_id])
    view = no_odds.view(cutoff)
    ens = build_model("ensemble", {"market_weight": 1.0}).fit(view).predict(fx, view)
    team = build_model("dixon_coles").fit(view).predict(fx, view)
    assert np.array_equal(ens.distribution.grid, team.distribution.grid)
    assert "ENSEMBLE_TEAM_MODEL_ONLY" in ens.warnings
    assert "MARKET_ODDS_UNAVAILABLE_FALLBACK" not in ens.warnings


def test_forecast_basis_and_stale_odds(league: tuple[MatchHistory, SyntheticTruth]) -> None:
    from super6.prediction.service import forecast_basis

    history, cutoff, fx = _setup(league)
    view = history.view(cutoff)
    ens = build_model("ensemble").fit(view).predict(fx, view)
    assert forecast_basis(ens) == "market odds"
    assert forecast_basis(build_model("dixon_coles").fit(view).predict(fx, view)) == \
        "results model"
    late = history.view(cutoff + pd.Timedelta(days=6))
    later_fx = FixtureSpec(fx.fixture_id, fx.competition, fx.home_team, fx.away_team,
                           (cutoff + pd.Timedelta(days=7)).to_pydatetime())
    stale = build_model("market_poisson").fit(late).predict(later_fx, late)
    assert any(w.startswith("MARKET_ODDS_STALE") for w in stale.warnings)


def test_rejects_bad_configuration() -> None:
    with pytest.raises(ValueError):
        EnsembleModel(market_weight=1.5)
    with pytest.raises(ValueError):
        EnsembleModel(market_weight=0.5, team_model="rolling_goals")


def test_components_use_documented_defaults() -> None:
    model = build_model("ensemble")
    assert isinstance(model, EnsembleModel)
    assert model.config["market_weight"] == 1.0
    assert model.config["team_params"]["ridge"] == build_model("dixon_coles").config["ridge"]
