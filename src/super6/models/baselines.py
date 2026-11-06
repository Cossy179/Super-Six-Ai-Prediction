"""Reference strategies (DESIGN.md §10.1). These are controls, not production candidates."""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd

from super6.domain import FixtureSpec
from super6.features.point_in_time import PointInTimeView
from super6.features.rolling_form import team_form
from super6.models.base import (
    FittedScoreModel,
    GridSettings,
    InsufficientDataError,
    Prediction,
    ScoreModel,
    window,
)
from super6.prediction.score_grid import ScoreDistribution

MIN_COMPETITION_MATCHES = 100


def _competition_goal_means(df: pd.DataFrame) -> tuple[dict[str, tuple[float, float]],
                                                        tuple[float, float]]:
    if df.empty:
        raise InsufficientDataError("no results in the baseline window")
    grouped = df.groupby("competition").agg(
        n=("fixture_id", "size"), home=("home_goals", "mean"), away=("away_goals", "mean")
    )
    per_comp = {
        str(c): (float(r["home"]), float(r["away"]))
        for c, r in grouped.iterrows()
        if r["n"] >= MIN_COMPETITION_MATCHES
    }
    pooled = (float(df["home_goals"].mean()), float(df["away_goals"].mean()))
    return per_comp, pooled


class _FittedLeagueAverage(FittedScoreModel):
    def __init__(self, name: str, config: dict[str, Any], view: PointInTimeView,
                 grid: GridSettings) -> None:
        super().__init__(name, config, view, grid)
        df = window(view.results, view.cutoff, config["max_history_days"])
        self.per_comp, self.pooled = _competition_goal_means(df)

    def means(self, fixture: FixtureSpec) -> tuple[float, float, list[str]]:
        warnings: list[str] = []
        if fixture.competition in self.per_comp:
            lam_h, lam_a = self.per_comp[fixture.competition]
        else:
            lam_h, lam_a = self.pooled
            warnings.append(f"COMPETITION_POOLED:{fixture.competition}")
        if fixture.neutral:
            lam_h = lam_a = (lam_h + lam_a) / 2.0
        return lam_h, lam_a, warnings

    def _predict(self, fixture: FixtureSpec, view: PointInTimeView) -> Prediction:
        lam_h, lam_a, warnings = self.means(fixture)
        return Prediction(fixture.fixture_id, self.name, self.grid.poisson(lam_h, lam_a),
                          lam_h, lam_a, tuple(warnings))


class LeagueAverageBaseline(ScoreModel):
    """Poisson using the competition's average home and away goals; no team information."""

    name: ClassVar[str] = "league_average"
    description: ClassVar[str] = "Competition-average home/away goal rates (no team strength)"

    def __init__(self, grid: GridSettings | None = None, *, max_history_days: float = 730
                 ) -> None:
        super().__init__(grid, max_history_days=max_history_days)

    def fit(self, view: PointInTimeView) -> FittedScoreModel:
        return _FittedLeagueAverage(self.name, self.config, view, self.grid)


class _FittedEmpirical(FittedScoreModel):
    def __init__(self, name: str, config: dict[str, Any], view: PointInTimeView,
                 grid: GridSettings) -> None:
        super().__init__(name, config, view, grid)
        df = window(view.results, view.cutoff, config["max_history_days"])
        if df.empty:
            raise InsufficientDataError("no results in the empirical-score window")
        self.max_goals = int(config["max_goals"])
        self.pseudo = float(config["pseudo_count"])
        self.pooled = self._distribution(df)
        self.per_comp = {
            str(c): self._distribution(g)
            for c, g in df.groupby("competition")
            if len(g) >= MIN_COMPETITION_MATCHES
        }

    def _distribution(self, df: pd.DataFrame) -> ScoreDistribution:
        side = self.max_goals + 1
        h = df["home_goals"].to_numpy()
        a = df["away_goals"].to_numpy()
        inside = (h <= self.max_goals) & (a <= self.max_goals)
        counts = np.zeros((side, side))
        np.add.at(counts, (h[inside], a[inside]), 1.0)
        counts += self.pseudo
        other = float((~inside).sum()) + self.pseudo  # explicit bucket for unseen big scores
        total = counts.sum() + other
        return ScoreDistribution(counts / total, other / total)

    def _predict(self, fixture: FixtureSpec, view: PointInTimeView) -> Prediction:
        warnings: list[str] = []
        dist = self.per_comp.get(fixture.competition)
        if dist is None:
            dist = self.pooled
            warnings.append(f"COMPETITION_POOLED:{fixture.competition}")
        lam_h, lam_a = dist.expected_goals()
        return Prediction(fixture.fixture_id, self.name, dist, lam_h, lam_a, tuple(warnings))


class EmpiricalScoreBaseline(ScoreModel):
    """Historical scoreline frequencies in the competition (the 'always 1–1' control)."""

    name: ClassVar[str] = "empirical_score"
    description: ClassVar[str] = "Historical scoreline frequencies per competition"
    explicit_tail: ClassVar[bool] = True  # pseudo-counted mass for scores beyond max_goals

    def __init__(self, grid: GridSettings | None = None, *, max_history_days: float = 730,
                 pseudo_count: float = 0.5, max_goals: int = 10) -> None:
        super().__init__(grid, max_history_days=max_history_days, pseudo_count=pseudo_count,
                         max_goals=max_goals)

    def fit(self, view: PointInTimeView) -> FittedScoreModel:
        return _FittedEmpirical(self.name, self.config, view, self.grid)


class _FittedRollingGoals(_FittedLeagueAverage):
    def __init__(self, name: str, config: dict[str, Any], view: PointInTimeView,
                 grid: GridSettings) -> None:
        super().__init__(name, config, view, grid)
        df = window(view.results, view.cutoff, config["max_history_days"])
        form = team_form(df, int(config["window"]))
        self.form: dict[str, dict[str, float]] = {
            str(team): {c: float(row[c]) for c in ("matches", "goals_for", "goals_against")}
            for team, row in form.to_dict(orient="index").items()
        }
        self.prior = float(config["prior_matches"])

    def _ratio(self, team: str, column: str, per_game: float) -> tuple[float, bool]:
        entry = self.form.get(team)
        if entry is None:
            return 1.0, False
        n, total = entry["matches"], entry[column]
        shrunk = (total + self.prior * per_game) / (n + self.prior)
        return shrunk / per_game, True

    def _predict(self, fixture: FixtureSpec, view: PointInTimeView) -> Prediction:
        base_h, base_a, warnings = self.means(fixture)
        per_game = (base_h + base_a) / 2.0
        att_h, seen_h = self._ratio(fixture.home_team, "goals_for", per_game)
        def_h, _ = self._ratio(fixture.home_team, "goals_against", per_game)
        att_a, seen_a = self._ratio(fixture.away_team, "goals_for", per_game)
        def_a, _ = self._ratio(fixture.away_team, "goals_against", per_game)
        for team, seen in ((fixture.home_team, seen_h), (fixture.away_team, seen_a)):
            if not seen:
                warnings.append(f"UNKNOWN_TEAM:{team}")
        lam_h = max(base_h * att_h * def_a, 0.05)
        lam_a = max(base_a * att_a * def_h, 0.05)
        return Prediction(fixture.fixture_id, self.name, self.grid.poisson(lam_h, lam_a),
                          lam_h, lam_a, tuple(warnings))


class RollingGoalsPoisson(ScoreModel):
    """Poisson whose rates scale league averages by each team's last-N goals for/against."""

    name: ClassVar[str] = "rolling_goals"
    description: ClassVar[str] = "League averages scaled by last-N goals for/against"

    def __init__(self, grid: GridSettings | None = None, *, window: int = 10,
                 prior_matches: float = 3, max_history_days: float = 400) -> None:
        super().__init__(grid, window=window, prior_matches=prior_matches,
                         max_history_days=max_history_days)

    def fit(self, view: PointInTimeView) -> FittedScoreModel:
        return _FittedRollingGoals(self.name, self.config, view, self.grid)
