"""Exact-score models and a registry that builds them from ``configs/model_defaults.yaml``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from super6.config import load_yaml
from super6.models.base import GridSettings, ScoreModel
from super6.models.baselines import (
    EmpiricalScoreBaseline,
    LeagueAverageBaseline,
    RollingGoalsPoisson,
)
from super6.models.calibration import (
    CatBoostCalibrated,
    CatBoostMarketCalibrated,
    RateCalibratedModel,
)
from super6.models.catboost_model import CatBoostGoalModel, CatBoostMarketModel
from super6.models.dixon_coles import DixonColesModel
from super6.models.ensemble import EnsembleModel
from super6.models.market import MarketImpliedPoisson
from super6.models.poisson import PoissonModel

MODEL_CLASSES: dict[str, type[ScoreModel]] = {
    cls.name: cls
    for cls in (
        DixonColesModel,
        PoissonModel,
        RollingGoalsPoisson,
        LeagueAverageBaseline,
        EmpiricalScoreBaseline,
        MarketImpliedPoisson,
        EnsembleModel,
        CatBoostGoalModel,
        CatBoostMarketModel,
        CatBoostCalibrated,
        CatBoostMarketCalibrated,
    )
}


def load_model_defaults() -> dict[str, Any]:
    return load_yaml("model_defaults.yaml")


def grid_settings(defaults: Mapping[str, Any] | None = None) -> GridSettings:
    defaults = defaults if defaults is not None else load_model_defaults()
    g = defaults.get("score_grid", {})
    return GridSettings(
        tail_tolerance=float(g.get("tail_tolerance", 1e-6)),
        min_goals=int(g.get("min_goals", 10)),
        max_goals=int(g.get("max_goals", 30)),
    )


def build_model(
    name: str,
    overrides: Mapping[str, Any] | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> ScoreModel:
    if name not in MODEL_CLASSES:
        raise KeyError(f"unknown model {name!r}; choose from {sorted(MODEL_CLASSES)}")
    defaults = defaults if defaults is not None else load_model_defaults()
    models_cfg = defaults.get("models", {})
    params = dict(models_cfg.get(name, {}))
    params.update(overrides or {})
    cls = MODEL_CLASSES[name]
    if issubclass(cls, RateCalibratedModel):
        params.setdefault("base_params", dict(models_cfg.get(cls.base_class.name, {})))
    if name == EnsembleModel.name:
        # Components use their own documented defaults unless explicitly overridden.
        team = params.get("team_model", "dixon_coles")
        params.setdefault("team_params", dict(models_cfg.get(team, {})))
        params.setdefault("market_params", dict(models_cfg.get(MarketImpliedPoisson.name, {})))
    if "families" in params:
        params["families"] = tuple(params["families"])
    if "rho_bounds" in params:
        params["rho_bounds"] = tuple(params["rho_bounds"])
    return MODEL_CLASSES[name](grid_settings(defaults), **params)


__all__ = ["MODEL_CLASSES", "ScoreModel", "build_model", "grid_settings", "load_model_defaults"]
