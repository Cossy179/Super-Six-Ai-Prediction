"""Market + team-strength mixture (DESIGN.md §10.7).

The forecast is a weighted arithmetic mixture of two complete score distributions::

    P(h, a) = (1 − w) · P_team(h, a) + w · P_market(h, a)

``w`` (``market_weight``) is a single non-negative weight fitted on a validation window
(``scripts/fit_ensemble_weight.py``), never on the test window it is reported on. When
no odds were available before the cutoff, the market component would fall back to the
league-average baseline. In that case the ensemble uses the team model alone and
says so in the warnings.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from super6.domain import FixtureSpec
from super6.features.point_in_time import PointInTimeView
from super6.models.base import FittedScoreModel, GridSettings, Prediction, ScoreModel
from super6.models.catboost_model import CatBoostGoalModel
from super6.models.dixon_coles import DixonColesModel
from super6.models.market import MarketImpliedDC, MarketImpliedPoisson
from super6.models.poisson import PoissonModel
from super6.prediction.score_grid import ScoreDistribution

MARKET_FALLBACK = "MARKET_ODDS_UNAVAILABLE_FALLBACK"
MARKET_MODELS: dict[str, type[MarketImpliedPoisson]] = {
    MarketImpliedPoisson.name: MarketImpliedPoisson,
    MarketImpliedDC.name: MarketImpliedDC,
}
TEAM_MODELS: dict[str, type[ScoreModel]] = {
    DixonColesModel.name: DixonColesModel,
    PoissonModel.name: PoissonModel,
    CatBoostGoalModel.name: CatBoostGoalModel,
}


class _FittedEnsemble(FittedScoreModel):
    def __init__(self, name: str, config: dict[str, Any], view: PointInTimeView,
                 grid: GridSettings, team: FittedScoreModel, market: FittedScoreModel,
                 weight: float) -> None:
        super().__init__(name, config, view, grid)
        self.team = team
        self.market = market
        self.weight = weight

    def _predict(self, fixture: FixtureSpec, view: PointInTimeView) -> Prediction:
        pt = self.team.predict(fixture, view)
        pm = self.market.predict(fixture, view)
        if MARKET_FALLBACK in pm.warnings or self.weight == 0.0:
            warnings = [*pt.warnings]
            if MARKET_FALLBACK in pm.warnings:
                warnings.append("ENSEMBLE_TEAM_MODEL_ONLY")
            return Prediction(fixture.fixture_id, self.name, pt.distribution, pt.lam_home,
                              pt.lam_away, tuple(warnings),
                              {"market_weight_applied": 0.0, "team": dict(pt.details)})
        w = self.weight
        if w >= 1.0:
            dist = pm.distribution  # no zero-weight padding from the team grid
        else:
            dist = ScoreDistribution.mixture([pt.distribution, pm.distribution], [1.0 - w, w])
        details = {
            "market_weight_applied": w,
            "team_model": self.team.name,
            "team_lambda": [pt.lam_home, pt.lam_away],
            "market_lambda": [pm.lam_home, pm.lam_away],
            "team": dict(pt.details),
            "market": dict(pm.details),
        }
        # The mean of a mixture is the weighted mean of the component means.
        return Prediction(
            fixture.fixture_id, self.name, dist,
            (1.0 - w) * pt.lam_home + w * pm.lam_home,
            (1.0 - w) * pt.lam_away + w * pm.lam_away,
            tuple(dict.fromkeys([*pt.warnings, *pm.warnings])), details,
        )

    def describe(self) -> dict[str, Any]:
        out = super().describe()
        out["components"] = {"team": self.team.describe(), "market": self.market.describe()}
        return out


class EnsembleModel(ScoreModel):
    name: ClassVar[str] = "ensemble"
    description: ClassVar[str] = (
        "Mixture of the team-strength model and the market-implied model (team model only "
        "when no pre-cutoff odds exist)"
    )

    def __init__(
        self,
        grid: GridSettings | None = None,
        *,
        market_weight: float,
        team_model: str = "dixon_coles",
        market_model: str = "market_poisson",
        team_params: Mapping[str, Any] | None = None,
        market_params: Mapping[str, Any] | None = None,
        fitted_on: str | None = None,
    ) -> None:
        if not 0.0 <= market_weight <= 1.0:
            raise ValueError("market_weight must be in [0, 1]")
        if team_model not in TEAM_MODELS:
            raise ValueError(f"team_model must be one of {sorted(TEAM_MODELS)}")
        if market_model not in MARKET_MODELS:
            raise ValueError(f"market_model must be one of {sorted(MARKET_MODELS)}")
        super().__init__(grid, market_weight=float(market_weight), team_model=team_model,
                         market_model=market_model,
                         team_params=dict(team_params or {}),
                         market_params=dict(market_params or {}), fitted_on=fitted_on)
        params = dict(team_params or {})
        for key in ("rho_bounds", "families"):
            if key in params:
                params[key] = tuple(params[key])
        self.team = TEAM_MODELS[team_model](self.grid, **params)
        self.market = MARKET_MODELS[market_model](self.grid, **dict(market_params or {}))

    def fit(self, view: PointInTimeView) -> FittedScoreModel:
        return _FittedEnsemble(self.name, self.config, view, self.grid, self.team.fit(view),
                               self.market.fit(view), float(self.config["market_weight"]))
