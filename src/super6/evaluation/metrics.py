"""Forecast-quality and competition metrics (DESIGN.md §13.3)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike, NDArray

from super6.domain import Result, result_of
from super6.prediction.score_grid import OutcomeProbabilities, ScoreDistribution

OUTCOME_ORDER = (Result.HOME, Result.DRAW, Result.AWAY)


def exact_log_loss(dist: ScoreDistribution, home_goals: int, away_goals: int) -> float:
    """Negative log probability of the observed exact score (tail-safe)."""
    return -dist.log_probability(home_goals, away_goals)


def _outcome_vector(out: OutcomeProbabilities) -> NDArray[np.float64]:
    v = np.array([out.home, out.draw, out.away], dtype=np.float64)
    normalised: NDArray[np.float64] = v / v.sum()  # tail's result unknown: use grid mass
    return normalised


def outcome_brier(out: OutcomeProbabilities, actual: Result) -> float:
    p = _outcome_vector(out)
    o = np.array([float(actual is r) for r in OUTCOME_ORDER])
    return float(np.sum((p - o) ** 2))


def ranked_probability_score(out: OutcomeProbabilities, actual: Result) -> float:
    """RPS over the ordered outcomes home < draw < away (lower is better)."""
    p = np.cumsum(_outcome_vector(out))[:-1]
    o = np.cumsum([float(actual is r) for r in OUTCOME_ORDER])[:-1]
    return float(np.sum((p - o) ** 2) / (len(OUTCOME_ORDER) - 1))


def outcome_log_loss(out: OutcomeProbabilities, actual: Result) -> float:
    p = _outcome_vector(out)
    return float(-np.log(max(p[OUTCOME_ORDER.index(actual)], 1e-15)))


def actual_result(home_goals: int, away_goals: int) -> Result:
    return result_of(home_goals, away_goals)


def calibration_table(probabilities: ArrayLike, hits: ArrayLike,
                      bins: Sequence[float] = (0, .05, .1, .15, .2, .3, .4, .5, .6, .7, .8, 1.0)
                      ) -> pd.DataFrame:
    """Mean forecast vs observed frequency per probability bin."""
    df = pd.DataFrame({"p": np.asarray(probabilities, float), "hit": np.asarray(hits, float)})
    df["bin"] = pd.cut(df["p"], bins=list(bins), include_lowest=True)
    table = df.groupby("bin", observed=True).agg(
        n=("p", "size"), mean_forecast=("p", "mean"), observed=("hit", "mean")
    )
    table["observed_se"] = np.sqrt(table["observed"] * (1 - table["observed"]) / table["n"])
    return table.reset_index()


def paired_bootstrap(diff: pd.Series, groups: pd.Series, resamples: int = 2000, seed: int = 7
                     ) -> tuple[float, float, float]:
    """Mean of ``diff`` with a 95% CI from resampling whole ``groups`` (e.g. match days).

    Block resampling respects dependence between fixtures predicted at the same cutoff.
    """
    frame = pd.DataFrame({"d": diff.to_numpy(), "g": groups.to_numpy()})
    sums = frame.groupby("g")["d"].agg(["sum", "size"])
    s = sums["sum"].to_numpy()
    n = sums["size"].to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(s), size=(resamples, len(s)))
    means = s[idx].sum(axis=1) / n[idx].sum(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(s.sum() / n.sum()), float(lo), float(hi)
