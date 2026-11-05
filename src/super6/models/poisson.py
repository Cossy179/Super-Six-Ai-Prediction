"""Time-decayed team-strength goal model (DESIGN.md §10.2), shared with Dixon–Coles.

For a match between home team ``h`` and away team ``a`` in competition ``c``::

    log λ_home = μ + home_c · [not neutral] + att_h − def_a
    log λ_away = μ + att_a − def_h

Parameters maximise the exponentially time-weighted log-likelihood (weight
``exp(−ξ · age_days)``) minus an L2 penalty ``ridge/2 · (‖att‖² + ‖def‖²)``. The penalty
makes the problem identifiable without a sum-to-zero constraint and shrinks low-data
teams (newly promoted, early season) towards the average. Teams from different divisions
of a pool are fitted together, so a promoted team carries its lower-division strength.

``DixonColesModel`` (see ``dixon_coles.py``) adds the low-score dependence parameter ρ.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    window,
)
from super6.prediction.score_grid import dixon_coles_distribution

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
LOW_DATA_WEIGHT = 5.0  # effective (decay-weighted) matches below which a team is flagged
MIN_TRAINING_MATCHES = 30


@dataclass(frozen=True)
class TrainingData:
    teams: tuple[str, ...]
    competitions: tuple[str, ...]
    home_idx: IntArray
    away_idx: IntArray
    comp_idx: IntArray
    home_goals: FloatArray
    away_goals: FloatArray
    weight: FloatArray
    team_weight: FloatArray

    @property
    def n_teams(self) -> int:
        return len(self.teams)

    @property
    def n_comps(self) -> int:
        return len(self.competitions)


def build_training_data(
    results: pd.DataFrame, cutoff: pd.Timestamp, xi_per_day: float, max_history_days: float
) -> TrainingData:
    df = window(results, cutoff, max_history_days)
    if len(df) < MIN_TRAINING_MATCHES:
        raise InsufficientDataError(
            f"only {len(df)} results in the {max_history_days:.0f}-day window before {cutoff}"
        )
    age_days = (cutoff - df["kickoff_utc"]).dt.total_seconds().to_numpy() / 86400.0
    weight = np.exp(-xi_per_day * np.maximum(age_days, 0.0))
    teams = tuple(sorted(set(df["home_team"]) | set(df["away_team"])))
    comps = tuple(sorted(set(df["competition"])))
    t_index = {t: i for i, t in enumerate(teams)}
    c_index = {c: i for i, c in enumerate(comps)}
    hi = df["home_team"].map(t_index).to_numpy(dtype=np.int64)
    ai = df["away_team"].map(t_index).to_numpy(dtype=np.int64)
    team_weight = np.bincount(hi, weight, len(teams)) + np.bincount(ai, weight, len(teams))
    return TrainingData(
        teams=teams,
        competitions=comps,
        home_idx=hi,
        away_idx=ai,
        comp_idx=df["competition"].map(c_index).to_numpy(dtype=np.int64),
        home_goals=df["home_goals"].to_numpy(dtype=np.float64),
        away_goals=df["away_goals"].to_numpy(dtype=np.float64),
        weight=weight,
        team_weight=team_weight,
    )


class _Objective:
    """Negative penalised log-likelihood and its analytic gradient (scaled by 1/Σw)."""

    def __init__(self, data: TrainingData, ridge: float, dixon_coles: bool) -> None:
        self.d = data
        self.ridge = ridge
        self.dc = dixon_coles
        self.total_weight = float(data.weight.sum())
        yh, ya = data.home_goals, data.away_goals
        self.m00 = (yh == 0) & (ya == 0)
        self.m01 = (yh == 0) & (ya == 1)
        self.m10 = (yh == 1) & (ya == 0)
        self.m11 = (yh == 1) & (ya == 1)

    def unpack(self, theta: FloatArray) -> tuple[FloatArray, FloatArray, float, FloatArray, float]:
        n, c = self.d.n_teams, self.d.n_comps
        att = theta[:n]
        dfn = theta[n : 2 * n]
        mu = float(theta[2 * n])
        home = theta[2 * n + 1 : 2 * n + 1 + c]
        rho = float(theta[2 * n + 1 + c]) if self.dc else 0.0
        return att, dfn, mu, home, rho

    def __call__(self, theta: FloatArray) -> tuple[float, FloatArray]:
        d = self.d
        att, dfn, mu, home, rho = self.unpack(theta)
        hi, ai = d.home_idx, d.away_idx
        log_lh = mu + home[d.comp_idx] + att[hi] - dfn[ai]
        log_la = mu + att[ai] - dfn[hi]
        lam_h = np.exp(log_lh)
        lam_a = np.exp(log_la)
        w = d.weight
        ll = d.home_goals * log_lh - lam_h + d.away_goals * log_la - lam_a
        g_lh = d.home_goals - lam_h
        g_la = d.away_goals - lam_a
        g_rho = 0.0
        if self.dc:
            tau = np.ones_like(lam_h)
            tau[self.m00] = 1.0 - lam_h[self.m00] * lam_a[self.m00] * rho
            tau[self.m01] = 1.0 + lam_h[self.m01] * rho
            tau[self.m10] = 1.0 + lam_a[self.m10] * rho
            tau[self.m11] = 1.0 - rho
            tau = np.maximum(tau, 1e-10)
            ll = ll + np.log(tau)
            dtau_lh = np.zeros_like(lam_h)
            dtau_la = np.zeros_like(lam_h)
            dtau_rho = np.zeros_like(lam_h)
            m = self.m00
            dtau_lh[m] = -lam_h[m] * lam_a[m] * rho
            dtau_la[m] = -lam_h[m] * lam_a[m] * rho
            dtau_rho[m] = -lam_h[m] * lam_a[m]
            m = self.m01
            dtau_lh[m] = lam_h[m] * rho
            dtau_rho[m] = lam_h[m]
            m = self.m10
            dtau_la[m] = lam_a[m] * rho
            dtau_rho[m] = lam_a[m]
            dtau_rho[self.m11] = -1.0
            g_lh = g_lh + dtau_lh / tau
            g_la = g_la + dtau_la / tau
            g_rho = float(np.sum(w * dtau_rho / tau))
        g_lh = w * g_lh
        g_la = w * g_la
        n = d.n_teams
        grad_att = np.bincount(hi, g_lh, n) + np.bincount(ai, g_la, n)
        grad_def = -np.bincount(ai, g_lh, n) - np.bincount(hi, g_la, n)
        grad_mu = g_lh.sum() + g_la.sum()
        grad_home = np.bincount(d.comp_idx, g_lh, d.n_comps)
        value = -float(np.sum(w * ll)) + 0.5 * self.ridge * float(att @ att + dfn @ dfn)
        grad = -np.concatenate([grad_att, grad_def, [grad_mu], grad_home])
        grad[:n] += self.ridge * att
        grad[n : 2 * n] += self.ridge * dfn
        if self.dc:
            grad = np.concatenate([grad, [-g_rho]])
        return value / self.total_weight, grad / self.total_weight


class FittedTeamStrength(FittedScoreModel):
    def __init__(
        self,
        name: str,
        config: dict[str, Any],
        view: PointInTimeView,
        grid: GridSettings,
        data: TrainingData,
        theta: FloatArray,
        dixon_coles: bool,
        optimisation: dict[str, Any],
    ) -> None:
        super().__init__(name, config, view, grid)
        objective = _Objective(data, float(config["ridge"]), dixon_coles)
        att, dfn, mu, home, rho = objective.unpack(theta)
        self.teams = data.teams
        self.competitions = data.competitions
        self.attack = dict(zip(data.teams, att.tolist(), strict=True))
        self.defence = dict(zip(data.teams, dfn.tolist(), strict=True))
        self.team_weight = dict(zip(data.teams, data.team_weight.tolist(), strict=True))
        self.mu = mu
        self.home_advantage = dict(zip(data.competitions, home.tolist(), strict=True))
        self.rho = rho
        self.dixon_coles = dixon_coles
        self.theta = theta
        self.optimisation = optimisation

    def ratings(self) -> pd.DataFrame:
        frame = pd.DataFrame(
            {
                "attack": pd.Series(self.attack),
                "defence": pd.Series(self.defence),
                "effective_matches": pd.Series(self.team_weight),
            }
        )
        frame["net"] = frame["attack"] + frame["defence"]
        return frame.sort_values("net", ascending=False)

    def rates(self, fixture: FixtureSpec) -> tuple[float, float, list[str]]:
        warnings: list[str] = []
        for team in (fixture.home_team, fixture.away_team):
            if team not in self.attack:
                warnings.append(f"UNKNOWN_TEAM:{team}")
            elif self.team_weight[team] < LOW_DATA_WEIGHT:
                warnings.append(f"LOW_DATA_TEAM:{team}")
        att_h = self.attack.get(fixture.home_team, 0.0)
        att_a = self.attack.get(fixture.away_team, 0.0)
        def_h = self.defence.get(fixture.home_team, 0.0)
        def_a = self.defence.get(fixture.away_team, 0.0)
        if fixture.competition in self.home_advantage:
            home = self.home_advantage[fixture.competition]
        else:
            home = float(np.mean(list(self.home_advantage.values())))
            warnings.append(f"COMPETITION_NOT_IN_MODEL:{fixture.competition}")
        if fixture.neutral:
            home = 0.0
        lam_h = float(np.exp(self.mu + home + att_h - def_a))
        lam_a = float(np.exp(self.mu + att_a - def_h))
        return lam_h, lam_a, warnings

    def _predict(self, fixture: FixtureSpec, view: PointInTimeView) -> Prediction:
        lam_h, lam_a, warnings = self.rates(fixture)
        rho = self.rho if self.dixon_coles else 0.0
        dist, rho_used = dixon_coles_distribution(
            lam_h,
            lam_a,
            rho,
            tail_tolerance=self.grid.tail_tolerance,
            min_goals=self.grid.min_goals,
            max_goals=self.grid.max_goals,
        )
        if self.dixon_coles and abs(rho_used - rho) > 1e-12:
            warnings.append("RHO_SHRUNK_FOR_FEASIBILITY")
        details = {
            "attack_home": self.attack.get(fixture.home_team, 0.0),
            "defence_home": self.defence.get(fixture.home_team, 0.0),
            "attack_away": self.attack.get(fixture.away_team, 0.0),
            "defence_away": self.defence.get(fixture.away_team, 0.0),
            "home_advantage": self.home_advantage.get(fixture.competition),
            "rho": rho_used,
        }
        return Prediction(fixture.fixture_id, self.name, dist, lam_h, lam_a, tuple(warnings),
                          details)

    def describe(self) -> dict[str, Any]:
        out = super().describe()
        out.update(
            {
                "n_teams": len(self.teams),
                "competitions": list(self.competitions),
                "mu": self.mu,
                "home_advantage": self.home_advantage,
                "rho": self.rho if self.dixon_coles else None,
                "optimisation": self.optimisation,
            }
        )
        return out


class PoissonModel(ScoreModel):
    name: ClassVar[str] = "poisson"
    description: ClassVar[str] = (
        "Independent Poisson with time-decayed team attack/defence and per-competition home "
        "advantage"
    )
    dixon_coles: ClassVar[bool] = False

    def __init__(
        self,
        grid: GridSettings | None = None,
        *,
        xi_per_day: float = 0.0019,
        max_history_days: float = 1095,
        ridge: float = 2.0,
        rho_bounds: tuple[float, float] = (-0.2, 0.1),
    ) -> None:
        config: dict[str, Any] = {
            "xi_per_day": xi_per_day,
            "max_history_days": max_history_days,
            "ridge": ridge,
        }
        if self.dixon_coles:
            config["rho_bounds"] = list(rho_bounds)
        super().__init__(grid, **config)

    def fit(
        self, view: PointInTimeView, warm_start: FittedTeamStrength | None = None
    ) -> FittedTeamStrength:
        cfg = self.config
        data = build_training_data(
            view.results, view.cutoff, cfg["xi_per_day"], cfg["max_history_days"]
        )
        ridge = float(cfg["ridge"])
        theta0 = self._initial_theta(data, warm_start)
        info: dict[str, Any] = {}
        # Stage 1: independent Poisson (convex). Stage 2 (Dixon–Coles only): add rho.
        poisson_obj = _Objective(data, ridge, dixon_coles=False)
        res = minimize(
            poisson_obj,
            theta0[: 2 * data.n_teams + 1 + data.n_comps],
            jac=True,
            method="L-BFGS-B",
            options={"maxiter": 2000, "ftol": 1e-13, "gtol": 1e-8},
        )
        theta = np.asarray(res.x, dtype=np.float64)
        info["poisson"] = {"success": bool(res.success), "iterations": int(res.nit),
                           "objective": float(res.fun)}
        if self.dixon_coles:
            dc_obj = _Objective(data, ridge, dixon_coles=True)
            lo, hi = cfg["rho_bounds"]
            rho0 = float(np.clip(theta0[-1], lo, hi))
            n_free = len(theta)
            bounds = [(None, None)] * n_free + [(lo, hi)]
            res = minimize(
                dc_obj,
                np.concatenate([theta, [rho0]]),
                jac=True,
                method="L-BFGS-B",
                bounds=bounds,
                options={"maxiter": 2000, "ftol": 1e-13, "gtol": 1e-8},
            )
            theta = np.asarray(res.x, dtype=np.float64)
            info["dixon_coles"] = {"success": bool(res.success), "iterations": int(res.nit),
                                   "objective": float(res.fun)}
        return FittedTeamStrength(
            self.name, cfg, view, self.grid, data, theta, self.dixon_coles, info
        )

    def _initial_theta(
        self, data: TrainingData, warm: FittedTeamStrength | None
    ) -> FloatArray:
        n, c = data.n_teams, data.n_comps
        size = 2 * n + 1 + c + (1 if self.dixon_coles else 0)
        theta = np.zeros(size)
        mean_goals = float(
            np.average((data.home_goals + data.away_goals) / 2.0, weights=data.weight)
        )
        theta[2 * n] = np.log(max(mean_goals, 0.1))
        theta[2 * n + 1 : 2 * n + 1 + c] = 0.25
        if self.dixon_coles:
            theta[-1] = -0.05
        if warm is not None:
            for i, team in enumerate(data.teams):
                theta[i] = warm.attack.get(team, 0.0)
                theta[n + i] = warm.defence.get(team, 0.0)
            theta[2 * n] = warm.mu
            for j, comp in enumerate(data.competitions):
                theta[2 * n + 1 + j] = warm.home_advantage.get(comp, 0.25)
            if self.dixon_coles and warm.dixon_coles:
                theta[-1] = warm.rho
        return theta
