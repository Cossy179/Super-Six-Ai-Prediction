"""Leak-free recalibration of predicted goal rates (DESIGN.md §10.7).

Experiment 003 found the CatBoost challengers overconfident: their goal rates were too
extreme. This wrapper fits, inside every refit and using only data before the cutoff, a
per-side Poisson recalibration of the base model's log-rates::

    log λ' = a + s · log λ        (s < 1 shrinks over-extreme rates towards the middle)

How the calibration data is produced at cutoff ``t``:

1. fit the base model as of ``t − calibration_days``;
2. forecast every match from that window match day by match day, each at its own
   pre-kick-off cutoff. These are genuinely out-of-sample forecasts;
3. fit ``(a, s)`` for home and away goals by Poisson maximum likelihood on those
   forecasts and their results;
4. refit the base model at ``t`` and apply ``(a, s)`` to its forecasts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.optimize import minimize

from super6.domain import FixtureSpec
from super6.features.point_in_time import PointInTimeView
from super6.models.base import (
    FittedScoreModel,
    GridSettings,
    InsufficientDataError,
    Prediction,
    ScoreModel,
)
from super6.models.catboost_model import CatBoostGoalModel, CatBoostMarketModel
from super6.timeutil import UK

SLOPE_BOUNDS = (0.2, 1.5)
FloatArray = NDArray[np.float64]


def fit_rate_calibration(log_rate: FloatArray, goals: FloatArray) -> tuple[float, float]:
    """Maximum-likelihood ``(a, s)`` for goals ~ Poisson(exp(a + s · log_rate))."""
    x = np.asarray(log_rate, dtype=np.float64)
    y = np.asarray(goals, dtype=np.float64)

    def nll(theta: FloatArray) -> tuple[float, FloatArray]:
        eta = theta[0] + theta[1] * x
        mu = np.exp(eta)
        value = float(np.sum(mu - y * eta)) / len(x)
        grad = np.array([np.sum(mu - y), np.sum((mu - y) * x)]) / len(x)
        return value, grad

    res = minimize(nll, np.array([0.0, 1.0]), jac=True, method="L-BFGS-B",
                   bounds=[(-2.0, 2.0), SLOPE_BOUNDS], options={"ftol": 1e-12, "gtol": 1e-9})
    return float(res.x[0]), float(res.x[1])


def _day_batches(results: pd.DataFrame, start: pd.Timestamp
                 ) -> list[tuple[pd.Timestamp, pd.DataFrame]]:
    """Match days after ``start`` with a pre-kick-off cutoff no earlier than ``start``."""
    window = results.loc[results["kickoff_utc"] > start].copy()
    window["local_date"] = window["kickoff_utc"].dt.tz_convert(UK).dt.date
    out = []
    for day, group in window.groupby("local_date", sort=True):
        if group["kickoff_time_known"].all():
            cutoff = group["kickoff_utc"].min() - pd.Timedelta(minutes=1)
        else:
            cutoff = pd.Timestamp(str(day), tz=UK).tz_convert("UTC")
        cutoff = max(cutoff, start)
        out.append((cutoff, group.loc[group["kickoff_utc"] > cutoff]))
    return out


class _FittedCalibrated(FittedScoreModel):
    def __init__(self, name: str, config: dict[str, Any], view: PointInTimeView,
                 grid: GridSettings, base: FittedScoreModel,
                 params: tuple[float, float, float, float], n_calibration: int,
                 note: str | None) -> None:
        super().__init__(name, config, view, grid)
        self.base = base
        self.params = params
        self.n_calibration = n_calibration
        self.note = note

    def _predict_many(self, fixtures: Sequence[FixtureSpec], view: PointInTimeView
                      ) -> list[Prediction]:
        a_h, s_h, a_a, s_a = self.params
        out = []
        for p in self.base.predict_many(fixtures, view):
            lam_h = float(np.clip(np.exp(a_h + s_h * np.log(p.lam_home)), 0.05, 8.0))
            lam_a = float(np.clip(np.exp(a_a + s_a * np.log(p.lam_away)), 0.05, 8.0))
            warnings = (*p.warnings, self.note) if self.note else p.warnings
            out.append(Prediction(p.fixture_id, self.name, self.grid.poisson(lam_h, lam_a),
                                  lam_h, lam_a, warnings,
                                  {**dict(p.details), "calibration": list(self.params),
                                   "uncalibrated_lambda": [p.lam_home, p.lam_away]}))
        return out

    def _predict(self, fixture: FixtureSpec, view: PointInTimeView) -> Prediction:
        return self._predict_many([fixture], view)[0]

    def describe(self) -> dict[str, Any]:
        out = super().describe()
        out.update({"calibration": {"home": self.params[:2], "away": self.params[2:],
                                    "fixtures": self.n_calibration, "note": self.note},
                    "base": self.base.describe()})
        return out


class RateCalibratedModel(ScoreModel):
    name: ClassVar[str]
    base_class: ClassVar[type[ScoreModel]]

    def __init__(self, grid: GridSettings | None = None, *,
                 base_params: Mapping[str, Any] | None = None,
                 calibration_days: float = 182, min_calibration_fixtures: int = 300) -> None:
        params = dict(base_params or {})
        if "families" in params:
            params["families"] = tuple(params["families"])
        super().__init__(grid, base_params=params, calibration_days=float(calibration_days),
                         min_calibration_fixtures=int(min_calibration_fixtures))
        self.base = self.base_class(self.grid, **params)

    def fit(self, view: PointInTimeView) -> FittedScoreModel:
        cfg = self.config
        start = view.cutoff - pd.Timedelta(days=cfg["calibration_days"])
        params, n, note = (0.0, 1.0, 0.0, 1.0), 0, None
        try:
            early = self.base.fit(view.at(start))
        except InsufficientDataError:
            early, note = None, "CALIBRATION_SKIPPED_INSUFFICIENT_HISTORY"
        if early is not None:
            rows: list[tuple[float, float, int, int]] = []
            results = view.results
            for cutoff, group in _day_batches(results, start):
                if group.empty:
                    continue
                neutral = (group["neutral"] if "neutral" in group.columns
                           else pd.Series(False, index=group.index))
                fixtures = [
                    FixtureSpec(str(fid), str(comp), str(home), str(away), ko.to_pydatetime(),
                                bool(neu))
                    for fid, comp, home, away, ko, neu in zip(
                        group["fixture_id"], group["competition"], group["home_team"],
                        group["away_team"], group["kickoff_utc"], neutral, strict=True)
                ]
                preds = early.predict_many(fixtures, view.at(cutoff))
                rows.extend((np.log(p.lam_home), np.log(p.lam_away), int(h), int(a))
                            for p, h, a in zip(preds, group["home_goals"], group["away_goals"],
                                               strict=True))
            n = len(rows)
            if n >= cfg["min_calibration_fixtures"]:
                arr = np.array(rows, dtype=np.float64)
                a_h, s_h = fit_rate_calibration(arr[:, 0], arr[:, 2])
                a_a, s_a = fit_rate_calibration(arr[:, 1], arr[:, 3])
                params = (a_h, s_h, a_a, s_a)
            else:
                note = "CALIBRATION_SKIPPED_TOO_FEW_FIXTURES"
        full = self.base.fit(view)
        return _FittedCalibrated(self.name, cfg, view, self.grid, full, params, n, note)


class CatBoostCalibrated(RateCalibratedModel):
    name: ClassVar[str] = "catboost_cal"
    description: ClassVar[str] = "CatBoost goal rates with leak-free Poisson recalibration"
    base_class: ClassVar[type[ScoreModel]] = CatBoostGoalModel
    refit_every_days: ClassVar[float | None] = CatBoostGoalModel.refit_every_days


class CatBoostMarketCalibrated(RateCalibratedModel):
    name: ClassVar[str] = "catboost_market_cal"
    description: ClassVar[str] = (
        "CatBoost with market inputs and leak-free Poisson recalibration"
    )
    base_class: ClassVar[type[ScoreModel]] = CatBoostMarketModel
    refit_every_days: ClassVar[float | None] = CatBoostMarketModel.refit_every_days
