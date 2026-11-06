"""Common interface for exact-score models.

``ScoreModel.fit(view)`` returns a ``FittedScoreModel`` that remembers the cutoff it was
trained at. ``FittedScoreModel.predict`` refuses to

* forecast a fixture that kicked off before the view's cutoff, and
* be used with a view whose cutoff is earlier than its own training cutoff,

either of which would let post-cutoff information into a forecast.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pandas as pd

from super6.domain import FixtureSpec
from super6.features.point_in_time import LeakageError, PointInTimeView
from super6.prediction.score_grid import DEFAULT_TAIL_TOLERANCE, ScoreDistribution


class CutoffAfterKickoffError(ValueError):
    """A forecast was requested for a fixture that had already kicked off."""


class InsufficientDataError(RuntimeError):
    """Not enough history in the view to fit the model."""


@dataclass(frozen=True)
class GridSettings:
    tail_tolerance: float = DEFAULT_TAIL_TOLERANCE
    min_goals: int = 10
    max_goals: int = 30

    def poisson(self, lam_home: float, lam_away: float) -> ScoreDistribution:
        return ScoreDistribution.independent_poisson(
            lam_home,
            lam_away,
            tail_tolerance=self.tail_tolerance,
            min_goals=self.min_goals,
            max_goals=self.max_goals,
        )


@dataclass(frozen=True)
class Prediction:
    fixture_id: str
    model: str
    distribution: ScoreDistribution
    lam_home: float
    lam_away: float
    warnings: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)


class FittedScoreModel(ABC):
    def __init__(
        self, name: str, config: Mapping[str, Any], view: PointInTimeView, grid: GridSettings
    ) -> None:
        self.name = name
        self.config = dict(config)
        self.cutoff: pd.Timestamp = view.cutoff
        self.training_fingerprint = view.fingerprint()
        self.n_training_matches = view.n_results
        self.grid = grid

    def _check(self, fixture: FixtureSpec, view: PointInTimeView) -> None:
        if view.cutoff < self.cutoff:
            raise LeakageError(
                f"{self.name} was fitted at {self.cutoff} but asked to predict as of "
                f"{view.cutoff}"
            )
        if pd.Timestamp(fixture.kickoff_utc) <= view.cutoff:
            raise CutoffAfterKickoffError(
                f"{fixture.fixture_id} kicked off at {fixture.kickoff_utc}, not after the "
                f"cutoff {view.cutoff}"
            )

    def predict(self, fixture: FixtureSpec, view: PointInTimeView) -> Prediction:
        self._check(fixture, view)
        return self._predict(fixture, view)

    def predict_many(self, fixtures: Sequence[FixtureSpec], view: PointInTimeView
                     ) -> list[Prediction]:
        """Forecast several fixtures at one cutoff (models may batch for speed)."""
        for fx in fixtures:
            self._check(fx, view)
        return self._predict_many(fixtures, view)

    def _predict_many(self, fixtures: Sequence[FixtureSpec], view: PointInTimeView
                      ) -> list[Prediction]:
        return [self._predict(fx, view) for fx in fixtures]

    @abstractmethod
    def _predict(self, fixture: FixtureSpec, view: PointInTimeView) -> Prediction: ...

    def describe(self) -> dict[str, Any]:
        return {
            "model": self.name,
            "config": self.config,
            "cutoff": self.cutoff.isoformat(),
            "training_fingerprint": self.training_fingerprint,
            "n_training_matches": self.n_training_matches,
        }


class ScoreModel(ABC):
    name: ClassVar[str]
    #: Minimum days between refits in walk-forward replays (None: the replay default).
    refit_every_days: ClassVar[float | None] = None
    #: Short human-readable description for reports and explanations.
    description: ClassVar[str] = ""
    #: True only for models whose tail is a deliberate "any other score" probability rather
    #: than a grid that is too small. Their forecasts warn instead of blocking when the tail
    #: exceeds the tolerance. Every other model is blocked (DESIGN §10.8).
    explicit_tail: ClassVar[bool] = False

    def __init__(self, grid: GridSettings | None = None, **config: Any) -> None:
        self.grid = grid or GridSettings()
        self.config = config

    @abstractmethod
    def fit(self, view: PointInTimeView) -> FittedScoreModel: ...


def window(results: pd.DataFrame, cutoff: pd.Timestamp, max_history_days: float) -> pd.DataFrame:
    """Results that kicked off within ``max_history_days`` before ``cutoff``."""
    start = cutoff - pd.Timedelta(days=float(max_history_days))
    return results.loc[results["kickoff_utc"] >= start]
