"""Exact-score probability distributions with explicit tail accounting (DESIGN.md §10.8).

A ``ScoreDistribution`` stores ``P(home=h, away=a)`` on a finite square grid plus the
probability mass of every score that falls outside the grid (``tail``). The grid is never
silently renormalised: ``grid.sum() + tail == 1`` is an invariant checked on construction.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.stats import poisson

from super6.domain import Result

MASS_TOLERANCE = 1e-9
DEFAULT_TAIL_TOLERANCE = 1e-6

FloatArray = NDArray[np.float64]


class ProbabilityIntegrityError(ValueError):
    """A distribution violates a probability invariant."""


@dataclass(frozen=True)
class OutcomeProbabilities:
    home: float
    draw: float
    away: float
    unattributed: float = 0.0  # tail mass whose result is not represented on the grid

    def of(self, result: Result) -> float:
        return {Result.HOME: self.home, Result.DRAW: self.draw, Result.AWAY: self.away}[result]

    def as_dict(self) -> dict[str, float]:
        return {"home": self.home, "draw": self.draw, "away": self.away}


@dataclass(frozen=True)
class ScoreLine:
    home_goals: int
    away_goals: int
    probability: float


@dataclass(frozen=True, eq=False)
class ScoreDistribution:
    grid: FloatArray
    tail: float

    def __post_init__(self) -> None:
        g = np.array(self.grid, dtype=np.float64, copy=True)
        if g.ndim != 2 or g.shape[0] != g.shape[1] or g.shape[0] < 2:
            raise ProbabilityIntegrityError(f"grid must be square with side >= 2, got {g.shape}")
        if not np.all(np.isfinite(g)) or not np.isfinite(self.tail):
            raise ProbabilityIntegrityError("probabilities must be finite")
        if g.min() < -1e-15:
            raise ProbabilityIntegrityError(f"negative probability {g.min():.3e} in grid")
        if self.tail < -MASS_TOLERANCE or self.tail > 1.0 + MASS_TOLERANCE:
            raise ProbabilityIntegrityError(f"tail mass {self.tail} outside [0, 1]")
        g[g < 0] = 0.0  # only float noise of magnitude <= 1e-15 can reach here
        total = float(g.sum()) + float(self.tail)
        if abs(total - 1.0) > MASS_TOLERANCE:
            raise ProbabilityIntegrityError(
                f"grid mass + tail = {total:.12f}; distributions are never silently renormalised"
            )
        g.setflags(write=False)
        object.__setattr__(self, "grid", g)
        object.__setattr__(self, "tail", max(float(self.tail), 0.0))

    # ------------------------------------------------------------------ constructors
    @classmethod
    def from_grid(cls, grid: FloatArray, tail: float | None = None) -> ScoreDistribution:
        """Build from a grid; ``tail`` defaults to the mass the grid does not cover."""
        g = np.asarray(grid, dtype=np.float64)
        if tail is None:
            tail = 1.0 - float(g.sum())
            if tail < 0 and tail > -MASS_TOLERANCE:
                tail = 0.0
        return cls(g, tail)

    @classmethod
    def independent_poisson(
        cls,
        lam_home: float,
        lam_away: float,
        *,
        tail_tolerance: float = DEFAULT_TAIL_TOLERANCE,
        min_goals: int = 10,
        max_goals: int = 30,
    ) -> ScoreDistribution:
        n = poisson_grid_size(lam_home, lam_away, tail_tolerance, min_goals, max_goals)
        k = np.arange(n + 1)
        grid = np.outer(poisson.pmf(k, lam_home), poisson.pmf(k, lam_away))
        return cls.from_grid(grid)

    @classmethod
    def mixture(
        cls, components: Sequence[ScoreDistribution], weights: Sequence[float]
    ) -> ScoreDistribution:
        w = np.asarray(weights, dtype=np.float64)
        if len(components) != len(w) or len(w) == 0:
            raise ValueError("need one weight per component")
        if (w < 0).any() or abs(w.sum() - 1.0) > 1e-12:
            raise ValueError("mixture weights must be non-negative and sum to one")
        side = max(c.size for c in components)
        grid = np.zeros((side, side))
        tail = 0.0
        for comp, weight in zip(components, w, strict=True):
            grid[: comp.size, : comp.size] += weight * comp.grid
            tail += weight * comp.tail
        return cls(grid, tail)

    # ------------------------------------------------------------------ accessors
    @property
    def size(self) -> int:
        return int(self.grid.shape[0])

    @property
    def max_goals(self) -> int:
        return self.size - 1

    def prob(self, home_goals: int, away_goals: int) -> float:
        if 0 <= home_goals < self.size and 0 <= away_goals < self.size:
            return float(self.grid[home_goals, away_goals])
        return 0.0

    def scorelines(self) -> Iterator[tuple[int, int]]:
        for h in range(self.size):
            for a in range(self.size):
                yield h, a

    def outcome_probabilities(self) -> OutcomeProbabilities:
        g = self.grid
        return OutcomeProbabilities(
            home=float(np.tril(g, -1).sum()),
            draw=float(np.trace(g)),
            away=float(np.triu(g, 1).sum()),
            unattributed=self.tail,
        )

    def result_probability(self, result: Result) -> float:
        return self.outcome_probabilities().of(result)

    def expected_goals(self) -> tuple[float, float]:
        """Expected goals implied by the grid (a lower bound when ``tail`` > 0)."""
        k = np.arange(self.size)
        return float(self.grid.sum(axis=1) @ k), float(self.grid.sum(axis=0) @ k)

    def top_scores(self, n: int = 5) -> list[ScoreLine]:
        """Most likely scorelines, ties broken by fewer total goals then fewer home goals."""
        cells = sorted(
            ((float(self.grid[h, a]), h, a) for h, a in self.scorelines()),
            key=lambda c: (-round(c[0], 15), c[1] + c[2], c[1]),
        )
        return [ScoreLine(h, a, p) for p, h, a in cells[:n]]

    def log_probability(self, home_goals: int, away_goals: int, floor: float = 1e-15) -> float:
        """Log probability of an observed score.

        Declared treatment for rare scores: a score outside the grid is assigned the whole
        tail mass (an upper bound on its probability), floored at ``floor``.
        """
        if 0 <= home_goals < self.size and 0 <= away_goals < self.size:
            p = float(self.grid[home_goals, away_goals])
        else:
            p = self.tail
        return float(np.log(max(p, floor)))

    def invariant_violations(
        self, tail_tolerance: float = DEFAULT_TAIL_TOLERANCE, tol: float = 1e-6
    ) -> list[str]:
        problems: list[str] = []
        if self.grid.min() < 0:
            problems.append("NEGATIVE_PROBABILITY")
        total = float(self.grid.sum()) + self.tail
        if abs(total - 1.0) > tol:
            problems.append(f"MASS_NOT_ONE({total:.9f})")
        out = self.outcome_probabilities()
        if abs(out.home + out.draw + out.away + self.tail - 1.0) > tol:
            problems.append("OUTCOMES_INCONSISTENT_WITH_GRID")
        if self.tail > tail_tolerance:
            problems.append(f"TAIL_ABOVE_TOLERANCE({self.tail:.2e})")
        return problems

    def to_records(self, min_probability: float = 0.0) -> list[dict[str, Any]]:
        return [
            {"home_goals": h, "away_goals": a, "probability": float(self.grid[h, a])}
            for h, a in self.scorelines()
            if self.grid[h, a] >= min_probability
        ]


def poisson_grid_size(
    lam_home: float, lam_away: float, tail_tolerance: float, min_goals: int, max_goals: int
) -> int:
    """Smallest side length (as max goals) whose excluded mass is below ``tail_tolerance``."""
    if not (lam_home > 0 and lam_away > 0 and np.isfinite(lam_home) and np.isfinite(lam_away)):
        raise ProbabilityIntegrityError(f"invalid goal rates ({lam_home}, {lam_away})")
    n = min_goals
    while n < max_goals:
        covered = poisson.cdf(n, lam_home) * poisson.cdf(n, lam_away)
        if 1.0 - covered <= tail_tolerance:
            break
        n += 1
    return n


def dixon_coles_tau(
    lam_home: float, lam_away: float, rho: float
) -> tuple[float, float, float, float]:
    """Dixon–Coles low-score multipliers for (0,0), (0,1), (1,0), (1,1)."""
    return (
        1.0 - lam_home * lam_away * rho,
        1.0 + lam_home * rho,
        1.0 + lam_away * rho,
        1.0 - rho,
    )


def feasible_rho(lam_home: float, lam_away: float, rho: float) -> float:
    """Shrink ``rho`` towards zero just enough that every tau multiplier is non-negative."""
    lo = max(-1.0 / lam_home, -1.0 / lam_away)
    hi = min(1.0, 1.0 / (lam_home * lam_away))
    return float(min(max(rho, lo), hi))


def dixon_coles_distribution(
    lam_home: float,
    lam_away: float,
    rho: float,
    *,
    tail_tolerance: float = DEFAULT_TAIL_TOLERANCE,
    min_goals: int = 10,
    max_goals: int = 30,
) -> tuple[ScoreDistribution, float]:
    """Dixon–Coles distribution. Returns the distribution and the ``rho`` actually applied.

    The tau adjustment moves mass among the four low scores without changing the total,
    so the tail equals that of the underlying independent Poisson grid.
    """
    base = ScoreDistribution.independent_poisson(
        lam_home, lam_away, tail_tolerance=tail_tolerance, min_goals=min_goals,
        max_goals=max_goals,
    )
    rho_used = feasible_rho(lam_home, lam_away, rho)
    t00, t01, t10, t11 = dixon_coles_tau(lam_home, lam_away, rho_used)
    grid = np.array(base.grid)
    grid[0, 0] *= t00
    grid[0, 1] *= t01
    grid[1, 0] *= t10
    grid[1, 1] *= t11
    grid[grid < 0] = 0.0  # exact zeros at the feasibility boundary can round below zero
    return ScoreDistribution(grid, base.tail), rho_used
