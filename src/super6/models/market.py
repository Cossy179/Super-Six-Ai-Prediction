"""Market-implied benchmark (DESIGN.md §10.6).

Bookmaker margin is removed proportionally, then independent-Poisson goal rates are
solved so that the model's home/draw/away (and, when available, over/under 2.5 goals)
probabilities match the market's. Home/draw/away odds alone do not pin down a unique
exact-score distribution: the Poisson form is the extra assumption, and it is stated in
every prediction's details.

Only odds whose ``available_at`` is at or before the cutoff are used. Without usable odds
the model falls back to the league-average baseline and flags the fixture.
"""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.optimize import least_squares
from scipy.stats import poisson

from super6.domain import FixtureSpec, MarketOdds
from super6.features.point_in_time import PointInTimeView
from super6.models.base import FittedScoreModel, GridSettings, Prediction, ScoreModel
from super6.models.baselines import _FittedLeagueAverage

_K = np.arange(16)
STALE_ODDS_DAYS = 4.0  # weekend odds are normally collected the Friday before


def devig(odds: MarketOdds) -> tuple[tuple[float, float, float], float | None]:
    """Proportionally normalised implied probabilities (1X2, and P(over 2.5) if quoted)."""
    inv = np.array([1 / odds.home, 1 / odds.draw, 1 / odds.away])
    hda = inv / inv.sum()
    over = None
    if odds.over25 is not None and odds.under25 is not None:
        ou = np.array([1 / odds.over25, 1 / odds.under25])
        over = float(ou[0] / ou.sum())
    return (float(hda[0]), float(hda[1]), float(hda[2])), over


def _model_probs(lam_h: float, lam_a: float) -> NDArray[np.float64]:
    grid = np.outer(poisson.pmf(_K, lam_h), poisson.pmf(_K, lam_a))
    home = np.tril(grid, -1).sum()
    draw = np.trace(grid)
    away = np.triu(grid, 1).sum()
    total = _K[:, None] + _K[None, :]
    over = 1.0 - grid[total <= 2].sum()
    probs: NDArray[np.float64] = np.array([home, draw, away, over], dtype=np.float64)
    return probs


def implied_rates(odds: MarketOdds) -> tuple[float, float, float]:
    """Solve for (λ_home, λ_away); also returns the root-mean-square fit error."""
    (p_h, p_d, p_a), p_over = devig(odds)
    target = np.array([p_h, p_d, p_a, p_over if p_over is not None else np.nan])
    use = ~np.isnan(target)

    def residual(x: NDArray[np.float64]) -> NDArray[np.float64]:
        diff: NDArray[np.float64] = _model_probs(float(np.exp(x[0])), float(np.exp(x[1]))) - target
        return diff[use]

    sol = least_squares(residual, x0=np.log([1.4, 1.1]), bounds=(np.log(0.05), np.log(8.0)),
                        xtol=1e-12, ftol=1e-12)
    rmse = float(np.sqrt(np.mean(sol.fun**2)))
    return float(np.exp(sol.x[0])), float(np.exp(sol.x[1])), rmse


class _FittedMarket(FittedScoreModel):
    def __init__(self, name: str, config: dict[str, Any], view: PointInTimeView,
                 grid: GridSettings, fallback: _FittedLeagueAverage) -> None:
        super().__init__(name, config, view, grid)
        self.fallback = fallback

    def _predict(self, fixture: FixtureSpec, view: PointInTimeView) -> Prediction:
        odds = view.latest_odds(fixture.fixture_id)
        if odds is None:
            fb = self.fallback._predict(fixture, view)
            return Prediction(fixture.fixture_id, self.name, fb.distribution, fb.lam_home,
                              fb.lam_away, (*fb.warnings, "MARKET_ODDS_UNAVAILABLE_FALLBACK"),
                              {"fallback": self.fallback.name})
        lam_h, lam_a, rmse = implied_rates(odds)
        age_days = (view.cutoff - pd.Timestamp(odds.available_at)).total_seconds() / 86400
        warnings = (f"MARKET_ODDS_STALE:{age_days:.0f}d",) if age_days > STALE_ODDS_DAYS else ()
        details = {
            "odds_available_at": odds.available_at.isoformat(),
            "odds_source": odds.source,
            "odds_type": odds.odds_type,
            "fit_rmse": rmse,
            "used_over_under": odds.over25 is not None,
            "assumption": "independent Poisson fitted to de-vigged market probabilities",
        }
        return Prediction(fixture.fixture_id, self.name, self.grid.poisson(lam_h, lam_a),
                          lam_h, lam_a, warnings, details)


class MarketImpliedPoisson(ScoreModel):
    name: ClassVar[str] = "market_poisson"
    description: ClassVar[str] = "Poisson rates implied by de-vigged pre-cutoff market odds"

    def __init__(self, grid: GridSettings | None = None, *, devig: str = "proportional",
                 fallback_history_days: float = 730) -> None:
        if devig != "proportional":
            raise ValueError("only proportional de-vigging is implemented")
        super().__init__(grid, devig=devig, fallback_history_days=fallback_history_days)

    def fit(self, view: PointInTimeView) -> FittedScoreModel:
        fallback = _FittedLeagueAverage(
            "league_average", {"max_history_days": self.config["fallback_history_days"]},
            view, self.grid,
        )
        return _FittedMarket(self.name, self.config, view, self.grid, fallback)
