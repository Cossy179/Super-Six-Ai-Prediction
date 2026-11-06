"""Point-in-time team features for machine-learning models (DESIGN.md §9).

The builder is event-driven. Results are *events* at their ``result_available_at`` time;
feature requests are *queries* at an ``as_of`` time. Events and queries are processed in
time order, so a query only ever sees results published at or before its ``as_of``. Training
rows are queried one minute before their own kick-off; live and replayed forecasts are
queried at the prediction cutoff.

Feature families (each can be switched off for ablations):

* ``elo``: goal-difference-weighted Elo with home advantage. New teams start at a
  tier-based rating.
* ``goals``: exponentially weighted goals for/against over two spans.
* ``shots``: the same for shots and shots on target.
* ``xg``: the same for xG, plus how many matches the xG average is based on. Only
  2026-27 has xG in the current source, so this is mostly missing by design.
* ``schedule``: rest days and matches in the previous 14 days.
* ``tier``: competition tier, and the change in tier since the team's previous match
  (promotion or relegation).

Missing values stay ``NaN``. Every average carries a count of the matches behind it.
"""

from __future__ import annotations

import copy
import math
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

FAMILIES = ("elo", "goals", "shots", "xg", "schedule", "tier")
MATCH_COUNT_CAP = 38
STATS = {
    "goals": (("goals_for", "home_goals", "away_goals"), ("goals_against", "away_goals",
                                                          "home_goals")),
    "shots": (("shots_for", "home_shots", "away_shots"), ("shots_against", "away_shots",
                                                          "home_shots"),
              ("sot_for", "home_sot", "away_sot"), ("sot_against", "away_sot", "home_sot")),
    "xg": (("xg_for", "home_xg", "away_xg"), ("xg_against", "away_xg", "home_xg")),
}


@dataclass(frozen=True)
class FeatureConfig:
    families: tuple[str, ...] = FAMILIES
    spans: tuple[int, ...] = (6, 20)
    elo_k: float = 20.0
    elo_home: float = 60.0
    elo_initial: float = 1500.0
    elo_tier_gap: float = 100.0
    rest_cap_days: float = 30.0

    def __post_init__(self) -> None:
        unknown = set(self.families) - set(FAMILIES)
        if unknown:
            raise ValueError(f"unknown feature families: {sorted(unknown)}")


@dataclass
class _Ewm:
    num: float = 0.0
    den: float = 0.0
    n: int = 0

    def update(self, x: float, alpha: float) -> None:
        self.num = (1 - alpha) * self.num + x
        self.den = (1 - alpha) * self.den + 1.0
        self.n += 1

    def mean(self) -> float:
        return self.num / self.den if self.den > 0 else math.nan


@dataclass
class _Team:
    elo: float
    ewm: dict[str, _Ewm] = field(default_factory=dict)
    kickoffs: deque[pd.Timestamp] = field(default_factory=lambda: deque(maxlen=12))
    last_tier: int | None = None
    matches: int = 0


class FeatureState:
    """Team states after applying every result published up to ``as_of``."""

    def __init__(self, tiers: Mapping[str, int], config: FeatureConfig) -> None:
        self.tiers = dict(tiers)
        self.cfg = config
        self.teams: dict[str, _Team] = {}
        self.as_of: pd.Timestamp | None = None
        self.applied: set[str] = set()

    def _tier(self, competition: str) -> int:
        return int(self.tiers.get(competition, max(self.tiers.values(), default=1)))

    def _team(self, team: str, competition: str) -> _Team:
        state = self.teams.get(team)
        if state is None:
            start = self.cfg.elo_initial - self.cfg.elo_tier_gap * (self._tier(competition) - 1)
            state = self.teams[team] = _Team(elo=start)
        return state

    # ------------------------------------------------------------------ events
    def apply(self, r: Mapping[str, Any]) -> None:
        if r["fixture_id"] in self.applied:
            return
        self.applied.add(str(r["fixture_id"]))
        comp = str(r["competition"])
        home, away = self._team(r["home_team"], comp), self._team(r["away_team"], comp)
        hg, ag = float(r["home_goals"]), float(r["away_goals"])
        hfa = 0.0 if bool(r.get("neutral", False)) else self.cfg.elo_home
        expected = 1.0 / (1.0 + 10 ** (-(home.elo + hfa - away.elo) / 400.0))
        actual = 1.0 if hg > ag else 0.0 if hg < ag else 0.5
        gd = abs(hg - ag)
        mult = 1.0 if gd <= 1 else 1.5 if gd == 2 else (11.0 + gd) / 8.0
        delta = self.cfg.elo_k * mult * (actual - expected)
        home.elo += delta
        away.elo -= delta
        for side, team in (("home", home), ("away", away)):
            for family in ("goals", "shots", "xg"):
                for name, home_col, away_col in STATS[family]:
                    x = r.get(home_col if side == "home" else away_col)
                    if x is None or (isinstance(x, float) and math.isnan(x)):
                        continue
                    for span in self.cfg.spans:
                        key = f"{name}_{span}"
                        team.ewm.setdefault(key, _Ewm()).update(float(x), 2.0 / (span + 1.0))
            team.kickoffs.append(pd.Timestamp(r["kickoff_utc"]))
            team.last_tier = self._tier(comp)
            team.matches += 1

    # ------------------------------------------------------------------ queries
    def features(self, home: str, away: str, competition: str, kickoff: pd.Timestamp,
                 neutral: bool = False) -> dict[str, Any]:
        cfg = self.cfg
        fam = set(cfg.families)
        tier = self._tier(competition)
        out: dict[str, Any] = {"competition": competition, "neutral": float(neutral)}
        sides = {"home": self.teams.get(home), "away": self.teams.get(away)}
        for side, st in sides.items():
            # Capped: an uncapped count grows with the calendar and would act as a date proxy.
            out[f"{side}_matches"] = float(min(st.matches, MATCH_COUNT_CAP)) if st else 0.0
        if "elo" in fam:
            start = cfg.elo_initial - cfg.elo_tier_gap * (tier - 1)
            eh = sides["home"].elo if sides["home"] else start
            ea = sides["away"].elo if sides["away"] else start
            hfa = 0.0 if neutral else cfg.elo_home
            out.update({"home_elo": eh, "away_elo": ea, "elo_diff": eh + hfa - ea,
                        "elo_expected_home": 1.0 / (1.0 + 10 ** (-(eh + hfa - ea) / 400.0))})
        for family in ("goals", "shots", "xg"):
            if family not in fam:
                continue
            for name, _, _ in STATS[family]:
                for span in cfg.spans:
                    key = f"{name}_{span}"
                    for side, st in sides.items():
                        e = st.ewm.get(key) if st else None
                        out[f"{side}_{key}"] = e.mean() if e else math.nan
            if family == "xg":
                for side, st in sides.items():
                    e = st.ewm.get(f"xg_for_{cfg.spans[0]}") if st else None
                    out[f"{side}_xg_matches"] = float(min(e.n, MATCH_COUNT_CAP)) if e else 0.0
        if "schedule" in fam:
            for side, st in sides.items():
                past = [k for k in (st.kickoffs if st else ()) if k < kickoff]
                rest = (kickoff - past[-1]).total_seconds() / 86400 if past else math.nan
                out[f"{side}_rest_days"] = min(rest, cfg.rest_cap_days) if past else math.nan
                out[f"{side}_matches_14d"] = float(
                    sum(1 for k in past if kickoff - k <= pd.Timedelta(days=14)))
        if "tier" in fam:
            out["tier"] = float(tier)
            for side, st in sides.items():
                last = st.last_tier if st and st.last_tier is not None else tier
                out[f"{side}_tier_change"] = float(tier - last)  # < 0 after promotion
        return out


def _ordered(results: pd.DataFrame) -> list[dict[str, Any]]:
    cols = [c for c in ("fixture_id", "competition", "home_team", "away_team", "home_goals",
                        "away_goals", "kickoff_utc", "result_available_at", "neutral",
                        "home_shots", "away_shots", "home_sot", "away_sot", "home_xg",
                        "away_xg") if c in results.columns]
    ordered = results.sort_values(["result_available_at", "kickoff_utc", "fixture_id"])
    return ordered[cols].to_dict(orient="records")  # type: ignore[return-value]


class FeatureBuilder:
    def __init__(self, tiers: Mapping[str, int], config: FeatureConfig | None = None) -> None:
        self.tiers = dict(tiers)
        self.config = config or FeatureConfig()

    def new_state(self) -> FeatureState:
        return FeatureState(self.tiers, self.config)

    def training_table(self, results: pd.DataFrame) -> tuple[pd.DataFrame, FeatureState]:
        """Pre-kick-off features for every result, plus the final state.

        Each row is queried one minute before its own kick-off, so it only uses results
        published earlier. Results are applied in publication order.
        """
        state = self.new_state()
        events = _ordered(results)
        queries = results.assign(
            as_of=results["kickoff_utc"] - pd.Timedelta(minutes=1)
        ).sort_values(["as_of", "fixture_id"])
        rows: list[dict[str, Any]] = []
        i = 0
        neutral = (queries["neutral"] if "neutral" in queries.columns
                   else pd.Series(False, index=queries.index))
        for fid, home, away, comp, kickoff, as_of, is_neutral in zip(
            queries["fixture_id"], queries["home_team"], queries["away_team"],
            queries["competition"], queries["kickoff_utc"], queries["as_of"], neutral,
            strict=True,
        ):
            while i < len(events) and events[i]["result_available_at"] <= as_of:
                state.apply(events[i])
                i += 1
            feats = state.features(str(home), str(away), str(comp), kickoff, bool(is_neutral))
            feats["fixture_id"] = fid
            rows.append(feats)
        for ev in events[i:]:
            state.apply(ev)
        state.as_of = (results["result_available_at"].max() if len(results)
                       else None)
        table = pd.DataFrame(rows).set_index("fixture_id")
        return table, state

    @staticmethod
    def advance(state: FeatureState, results: pd.DataFrame, as_of: pd.Timestamp
                ) -> FeatureState:
        """Apply results published up to ``as_of`` that ``state`` has not seen yet."""
        new = results.loc[(results["result_available_at"] <= as_of)
                          & ~results["fixture_id"].isin(state.applied)]
        for ev in _ordered(new):
            state.apply(ev)
        state.as_of = as_of
        return state


def snapshot(state: FeatureState) -> FeatureState:
    return copy.deepcopy(state)


def feature_matrix(rows: Iterable[Mapping[Any, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(list(rows))
    for col in frame.columns:
        if col != "competition":
            frame[col] = pd.to_numeric(frame[col], errors="coerce").astype(np.float64)
    return frame
