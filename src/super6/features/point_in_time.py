"""Point-in-time access to match history (DESIGN.md §8.4).

Models never see the full history. They receive a ``PointInTimeView`` that exposes only

* results whose ``result_available_at <= cutoff`` (never the target fixture's result),
* goal minutes of those results only, and
* market odds whose ``available_at <= cutoff``.

This is the single choke point that enforces ``feature_available_at <= prediction_cutoff_at``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from super6.domain import FixtureStatus, MarketOdds
from super6.timeutil import to_timestamp

MATCH_COLUMNS = [
    "fixture_id",
    "competition",
    "season",
    "kickoff_utc",
    "kickoff_time_known",
    "home_team",
    "away_team",
    "status",
    "home_goals",
    "away_goals",
    "result_available_at",
]
# Optional post-match statistics. They describe a finished match, so they become visible
# exactly when its result does (filtered by result_available_at like everything else).
STAT_COLUMNS = ["home_shots", "away_shots", "home_sot", "away_sot", "home_xg", "away_xg"]
ODDS_COLUMNS = [
    "fixture_id",
    "odds_type",
    "source",
    "available_at",
    "home",
    "draw",
    "away",
    "over25",
    "under25",
]

# Goal minutes (Golden Goal model). Like statistics, they become visible with the result.
GOAL_COLUMNS = ["fixture_id", "period", "match_minute", "stoppage_minute", "team_side"]


class LeakageError(RuntimeError):
    """Information from after the prediction cutoff reached a model."""


def empty_odds_frame() -> pd.DataFrame:
    frame = pd.DataFrame({c: pd.Series(dtype="object") for c in ODDS_COLUMNS})
    frame["available_at"] = pd.Series(dtype="datetime64[ns, UTC]")
    for c in ("home", "draw", "away", "over25", "under25"):
        frame[c] = pd.Series(dtype="float64")
    return frame


def empty_goals_frame() -> pd.DataFrame:
    frame = pd.DataFrame({c: pd.Series(dtype="int64") for c in GOAL_COLUMNS})
    frame["fixture_id"] = pd.Series(dtype="object")
    frame["team_side"] = pd.Series(dtype="object")
    frame["stoppage_minute"] = pd.Series(dtype="float64")
    return frame


@dataclass
class MatchHistory:
    """Canonical matches, timestamped odds snapshots and goal minutes (timestamps UTC)."""

    matches: pd.DataFrame
    odds: pd.DataFrame = field(default_factory=empty_odds_frame)
    goals: pd.DataFrame = field(default_factory=empty_goals_frame)

    def __post_init__(self) -> None:
        missing = [c for c in MATCH_COLUMNS if c not in self.matches.columns]
        if missing:
            raise ValueError(f"match history missing columns: {missing}")
        missing = [c for c in ODDS_COLUMNS if c not in self.odds.columns]
        if missing:
            raise ValueError(f"odds history missing columns: {missing}")
        m = self.matches.copy()
        for col in STAT_COLUMNS:
            m[col] = pd.to_numeric(m[col], errors="coerce") if col in m.columns else np.nan
        if "neutral" not in m.columns:
            m["neutral"] = False
        m["neutral"] = m["neutral"].fillna(False).astype(bool)
        for col in ("kickoff_utc", "result_available_at"):
            m[col] = pd.to_datetime(m[col], utc=True)
        m["kickoff_time_known"] = m["kickoff_time_known"].astype(bool)
        finished = m["status"] == FixtureStatus.FINISHED.value
        if m.loc[finished, ["home_goals", "away_goals"]].isna().any().any():
            raise ValueError("finished matches must have goals")
        if m["fixture_id"].duplicated().any():
            dupes = m.loc[m["fixture_id"].duplicated(), "fixture_id"].tolist()[:5]
            raise ValueError(f"duplicate fixture ids in history: {dupes}")
        self.matches = m.sort_values(["kickoff_utc", "fixture_id"]).reset_index(drop=True)
        o = self.odds.copy()
        o["available_at"] = pd.to_datetime(o["available_at"], utc=True)
        self.odds = o.sort_values(["fixture_id", "available_at"]).reset_index(drop=True)
        missing = [c for c in GOAL_COLUMNS if c not in self.goals.columns]
        if missing:
            raise ValueError(f"goal history missing columns: {missing}")
        self.goals = self.goals[GOAL_COLUMNS].reset_index(drop=True)
        self._odds_groups: dict[str, pd.DataFrame] | None = None

    def odds_for(self, fixture_id: str) -> pd.DataFrame | None:
        if self._odds_groups is None:
            self._odds_groups = {str(k): g for k, g in self.odds.groupby("fixture_id", sort=False)}
        return self._odds_groups.get(fixture_id)

    def with_extra(
        self, matches: pd.DataFrame | None = None, odds: pd.DataFrame | None = None
    ) -> MatchHistory:
        """A new history with extra rows appended (used by tests and round overrides)."""
        new_matches = self.matches if matches is None else pd.concat(
            [self.matches, matches.reindex(columns=self.matches.columns)], ignore_index=True
        )
        new_odds = self.odds if odds is None or odds.empty else pd.concat(
            [self.odds, odds[ODDS_COLUMNS]], ignore_index=True
        )
        return MatchHistory(new_matches, new_odds, self.goals)

    def view(self, cutoff: datetime | pd.Timestamp | str) -> PointInTimeView:
        return PointInTimeView(self, cutoff)


class PointInTimeView:
    """What was knowable at ``cutoff``. Construct via ``MatchHistory.view``."""

    def __init__(self, history: MatchHistory, cutoff: datetime | pd.Timestamp | str) -> None:
        self.cutoff: pd.Timestamp = to_timestamp(cutoff)
        m = history.matches
        mask = (m["status"] == FixtureStatus.FINISHED.value) & (
            m["result_available_at"] <= self.cutoff
        )
        results = m.loc[mask].copy()
        results["home_goals"] = results["home_goals"].astype(np.int64)
        results["away_goals"] = results["away_goals"].astype(np.int64)
        self._results = results.reset_index(drop=True)
        self._history = history
        if len(self._results) and self._results["result_available_at"].max() > self.cutoff:
            raise LeakageError("result published after cutoff entered the view")
        self._fingerprint: str | None = None

    @property
    def results(self) -> pd.DataFrame:
        """Finished matches whose results were public at the cutoff (a copy)."""
        return self._results.copy()

    @property
    def goal_events(self) -> pd.DataFrame:
        """Goal minutes of the matches in ``results`` only (a copy)."""
        goals = self._history.goals
        return goals.loc[goals["fixture_id"].isin(self._results["fixture_id"])].reset_index(
            drop=True)

    @property
    def n_results(self) -> int:
        return len(self._results)

    def latest_odds(self, fixture_id: str, odds_type: str | None = None) -> MarketOdds | None:
        """Most recent odds snapshot for ``fixture_id`` available at the cutoff."""
        return self.odds_as_of(fixture_id, self.cutoff, odds_type)

    def odds_as_of(self, fixture_id: str, as_of: pd.Timestamp | datetime,
                   odds_type: str | None = None) -> MarketOdds | None:
        """Most recent snapshot available at ``as_of`` (which may not exceed the cutoff).

        Used to rebuild what a historical training row knew before its own kick-off.
        """
        as_of_ts = to_timestamp(as_of)
        if as_of_ts > self.cutoff:
            raise LeakageError(f"odds requested as of {as_of_ts}, after the cutoff {self.cutoff}")
        group = self._history.odds_for(fixture_id)
        if group is None:
            return None
        mask = group["available_at"] <= as_of_ts
        if odds_type is not None:
            mask &= group["odds_type"] == odds_type
        usable = group.loc[mask]
        if usable.empty:
            return None
        row = usable.iloc[-1]
        return MarketOdds(
            home=float(row["home"]),
            draw=float(row["draw"]),
            away=float(row["away"]),
            over25=None if pd.isna(row["over25"]) else float(row["over25"]),
            under25=None if pd.isna(row["under25"]) else float(row["under25"]),
            available_at=row["available_at"].to_pydatetime(),
            source=str(row["source"]),
            odds_type=str(row["odds_type"]),
        )

    def at(self, cutoff: datetime | pd.Timestamp | str) -> PointInTimeView:
        """A view at an *earlier* cutoff (it can only ever know less than this one)."""
        earlier = to_timestamp(cutoff)
        if earlier > self.cutoff:
            raise LeakageError(f"view at {earlier} would see beyond the cutoff {self.cutoff}")
        return PointInTimeView(self._history, earlier)

    def fingerprint(self) -> str:
        """Stable hash of the cutoff and every result visible in the view."""
        if self._fingerprint is None:
            h = hashlib.sha256(self.cutoff.isoformat().encode())
            if len(self._results):
                cols = self._results[
                    ["fixture_id", "kickoff_utc", "home_team", "away_team", "home_goals",
                     "away_goals"]
                ]
                h.update(pd.util.hash_pandas_object(cols, index=False).to_numpy().tobytes())
            self._fingerprint = h.hexdigest()
        return self._fingerprint
