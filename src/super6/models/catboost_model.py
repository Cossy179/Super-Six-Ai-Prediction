"""CatBoost goal-rate challengers (DESIGN.md §10.5).

Two gradient-boosted regressors with a Poisson loss predict home and away goals from
point-in-time team features (``features/team_features.py``). Their rates define an
independent-Poisson score grid, so the output is a coherent exact-score distribution, not
three separately trained result classes.

* ``catboost``: results and match statistics only.
* ``catboost_market``: the same features plus de-vigged pre-match market probabilities.
  Training rows use the pre-match odds each historical match had before its kick-off,
  never closing odds.

The learned mapping is refitted every ``refit_every_days`` (28 by default). Team features
are advanced to each prediction cutoff, so form is never stale.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Sequence
from functools import lru_cache
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from super6.config import load_yaml
from super6.domain import FixtureSpec, MarketOdds
from super6.features.point_in_time import PointInTimeView
from super6.features.team_features import (
    FAMILIES,
    FeatureBuilder,
    FeatureConfig,
    FeatureState,
    feature_matrix,
)
from super6.models.base import (
    FittedScoreModel,
    GridSettings,
    InsufficientDataError,
    Prediction,
    ScoreModel,
)
from super6.models.market import devig, implied_rates

MARKET_COLUMNS = ["mkt_log_lam_home", "mkt_log_lam_away", "mkt_p_home", "mkt_p_draw",
                  "mkt_p_away", "mkt_p_over25"]
MIN_TRAINING_ROWS = 200


@lru_cache(maxsize=200_000)
def _market_values(home: float, draw: float, away: float, over: float | None,
                   under: float | None) -> tuple[float, ...]:
    odds = MarketOdds(home=home, draw=draw, away=away, over25=over, under25=under,
                      available_at=pd.Timestamp("2000-01-01", tz="UTC").to_pydatetime())
    (ph, pd_, pa), p_over = devig(odds)
    lam_h, lam_a, _ = implied_rates(odds)
    return (math.log(lam_h), math.log(lam_a), ph, pd_, pa,
            p_over if p_over is not None else math.nan)


def market_features(odds: MarketOdds | None) -> dict[str, float]:
    if odds is None:
        return dict.fromkeys(MARKET_COLUMNS, math.nan)
    vals = _market_values(odds.home, odds.draw, odds.away, odds.over25, odds.under25)
    return dict(zip(MARKET_COLUMNS, vals, strict=True))


def _tiers() -> dict[str, int]:
    comps = load_yaml("leagues.yaml")["competitions"]
    return {code: int(info["tier"]) for code, info in comps.items()}


class _FittedCatBoost(FittedScoreModel):
    def __init__(self, name: str, config: dict[str, Any], view: PointInTimeView,
                 grid: GridSettings, builder: FeatureBuilder, state: FeatureState,
                 models: tuple[Any, Any], columns: list[str], use_market: bool,
                 min_team_matches: int, n_rows: int,
                 fallback: tuple[tuple[Any, Any], list[str]] | None = None) -> None:
        super().__init__(name, config, view, grid)
        self.builder = builder
        self._base = state
        self._live = copy.deepcopy(state)
        self._live_as_of = view.cutoff
        self.models = models
        self.columns = columns
        self.use_market = use_market
        self.min_team_matches = min_team_matches
        self.n_rows = n_rows
        self.fallback = fallback

    def _state_for(self, view: PointInTimeView) -> FeatureState:
        if view.cutoff < self._live_as_of:
            self._live = copy.deepcopy(self._base)
        if view.cutoff != self._live_as_of or view.cutoff < self._live_as_of:
            FeatureBuilder.advance(self._live, view.results, view.cutoff)
            self._live_as_of = view.cutoff
        return self._live

    def _rows(self, fixtures: Sequence[FixtureSpec], view: PointInTimeView
              ) -> tuple[pd.DataFrame, list[list[str]], list[bool]]:
        state = self._state_for(view)
        rows, warnings, has_market = [], [], []
        for fx in fixtures:
            feats = state.features(fx.home_team, fx.away_team, fx.competition,
                                   pd.Timestamp(fx.kickoff_utc), fx.neutral)
            warn = []
            for side, team in (("home", fx.home_team), ("away", fx.away_team)):
                if feats[f"{side}_matches"] == 0:
                    warn.append(f"UNKNOWN_TEAM:{team}")
                elif feats[f"{side}_matches"] < self.min_team_matches:
                    warn.append(f"LOW_DATA_TEAM:{team}")
            if self.use_market:
                odds = view.latest_odds(fx.fixture_id, odds_type="prematch")
                feats.update(market_features(odds))
                if odds is None:
                    warn.append("MARKET_FEATURES_MISSING_RESULTS_FALLBACK")
            has_market.append(not self.use_market or ("mkt_p_home" in feats
                              and not math.isnan(feats["mkt_p_home"])))
            rows.append(feats)
            warnings.append(warn)
        frame = feature_matrix(rows).reindex(columns=self.columns)
        return frame, warnings, has_market

    def _predict_many(self, fixtures: Sequence[FixtureSpec], view: PointInTimeView
                      ) -> list[Prediction]:
        if not fixtures:
            return []
        X, warnings, has_market = self._rows(fixtures, view)
        lam_h = self.models[0].predict(X, prediction_type="Exponent")
        lam_a = self.models[1].predict(X, prediction_type="Exponent")
        missing = ~np.asarray(has_market, dtype=bool)
        if missing.any():
            # Rows without odds were rare in training, so the market model's handling of
            # them is unreliable; use the results-only twin fitted on the same rows.
            assert self.fallback is not None
            (fb_h, fb_a), fb_cols = self.fallback
            Xf = X.loc[missing, fb_cols]
            lam_h[missing] = fb_h.predict(Xf, prediction_type="Exponent")
            lam_a[missing] = fb_a.predict(Xf, prediction_type="Exponent")
        lam_h = np.clip(lam_h, 0.05, 8.0)
        lam_a = np.clip(lam_a, 0.05, 8.0)
        return [
            Prediction(fx.fixture_id, self.name, self.grid.poisson(float(h), float(a)),
                       float(h), float(a), tuple(w), {"n_training_rows": self.n_rows})
            for fx, h, a, w in zip(fixtures, lam_h, lam_a, warnings, strict=True)
        ]

    def _predict(self, fixture: FixtureSpec, view: PointInTimeView) -> Prediction:
        return self._predict_many([fixture], view)[0]

    def importances(self) -> pd.DataFrame:
        return pd.DataFrame({
            "home_goals": self.models[0].get_feature_importance(),
            "away_goals": self.models[1].get_feature_importance(),
        }, index=self.columns).sort_values("home_goals", ascending=False)


class CatBoostGoalModel(ScoreModel):
    name: ClassVar[str] = "catboost"
    description: ClassVar[str] = "CatBoost Poisson goal rates from point-in-time team features"
    refit_every_days: ClassVar[float] = 28.0
    use_market: ClassVar[bool] = False

    def __init__(
        self,
        grid: GridSettings | None = None,
        *,
        families: Sequence[str] = FAMILIES,
        iterations: int = 600,
        depth: int = 4,
        learning_rate: float = 0.05,
        l2_leaf_reg: float = 3.0,
        xi_per_day: float = 0.001,
        max_history_days: float = 1460,
        min_team_matches: int = 3,
        seed: int = 0,
        thread_count: int = 4,
    ) -> None:
        super().__init__(grid, families=list(families), iterations=iterations, depth=depth,
                         learning_rate=learning_rate, l2_leaf_reg=l2_leaf_reg,
                         xi_per_day=xi_per_day, max_history_days=max_history_days,
                         min_team_matches=min_team_matches, seed=seed,
                         thread_count=thread_count, use_market=self.use_market)
        self.feature_config = FeatureConfig(families=tuple(families))

    def _regressor(self) -> Any:
        from catboost import CatBoostRegressor

        c = self.config
        return CatBoostRegressor(
            loss_function="Poisson", iterations=c["iterations"], depth=c["depth"],
            learning_rate=c["learning_rate"], l2_leaf_reg=c["l2_leaf_reg"],
            random_seed=c["seed"], thread_count=c["thread_count"], verbose=False,
            allow_writing_files=False,
        )

    def fit(self, view: PointInTimeView) -> FittedScoreModel:
        cfg = self.config
        builder = FeatureBuilder(_tiers(), self.feature_config)
        results = view.results
        table, state = builder.training_table(results)
        start = view.cutoff - pd.Timedelta(days=float(cfg["max_history_days"]))
        train = results.loc[results["kickoff_utc"] >= start].set_index("fixture_id")
        X = table.loc[train.index].reset_index(drop=True)
        enough = ((X["home_matches"] >= cfg["min_team_matches"])
                  & (X["away_matches"] >= cfg["min_team_matches"])).to_numpy()
        if self.use_market:
            mkt = [market_features(view.odds_as_of(fid, kickoff - pd.Timedelta(minutes=1),
                                                   odds_type="prematch"))
                   for fid, kickoff in zip(train.index, train["kickoff_utc"], strict=True)]
            X = pd.concat([X, pd.DataFrame(mkt)], axis=1)
        X = feature_matrix(X.to_dict(orient="records"))
        X, train = X.loc[enough].reset_index(drop=True), train.loc[enough]
        if len(X) < MIN_TRAINING_ROWS:
            raise InsufficientDataError(f"only {len(X)} usable training rows for {self.name}")
        age = (view.cutoff - train["kickoff_utc"]).dt.total_seconds().to_numpy() / 86400.0
        weight = np.exp(-float(cfg["xi_per_day"]) * age)
        columns = list(X.columns)
        models = self._fit_pair(X, train, weight)
        fallback = None
        if self.use_market:
            base_cols = [c for c in columns if c not in MARKET_COLUMNS]
            fallback = (self._fit_pair(X[base_cols], train, weight), base_cols)
        return _FittedCatBoost(self.name, cfg, view, self.grid, builder, state, models,
                               columns, self.use_market, int(cfg["min_team_matches"]), len(X),
                               fallback)

    def _fit_pair(self, X: pd.DataFrame, train: pd.DataFrame, weight: np.ndarray
                  ) -> tuple[Any, Any]:
        cat = [list(X.columns).index("competition")]
        fitted = []
        for target in ("home_goals", "away_goals"):
            reg = self._regressor()
            reg.fit(X, train[target].to_numpy(dtype=float), cat_features=cat,
                    sample_weight=weight)
            fitted.append(reg)
        return fitted[0], fitted[1]


class CatBoostMarketModel(CatBoostGoalModel):
    name: ClassVar[str] = "catboost_market"
    description: ClassVar[str] = (
        "CatBoost Poisson goal rates from team features plus pre-match market probabilities"
    )
    use_market: ClassVar[bool] = True
