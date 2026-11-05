"""Chronological walk-forward replay (DESIGN.md §13.1).

For each match day in the test window:

1. the cutoff is one minute before that day's earliest kick-off (local midnight when
   kick-off times are unknown);
2. every model sees only ``history.view(cutoff)``;
3. models are refitted (from scratch) when their last fit is ``refit_every_days`` old;
   re-using an older fit is leak-free, it simply knows less;
4. each fixture is forecast, picks are made for both modes, and only then is the actual
   result read to score them.

Every model forecasts exactly the same fixtures at the same cutoffs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd

from super6.domain import FixtureSpec, FixtureStatus
from super6.evaluation.metrics import (
    actual_result,
    exact_log_loss,
    outcome_brier,
    outcome_log_loss,
    ranked_probability_score,
)
from super6.features.point_in_time import MatchHistory
from super6.models.base import FittedScoreModel, ScoreModel
from super6.prediction.optimiser import Objective, choose_score, super6_points
from super6.rules import CompetitionRules
from super6.timeutil import UK


@dataclass(frozen=True)
class Batch:
    batch_id: str
    cutoff: pd.Timestamp
    fixtures: tuple[FixtureSpec, ...]
    actual: tuple[tuple[int, int], ...]


def make_batches(history: MatchHistory, start: pd.Timestamp, end: pd.Timestamp,
                 competitions: list[str] | None = None) -> list[Batch]:
    m = history.matches
    mask = (m["status"] == FixtureStatus.FINISHED.value) & (m["kickoff_utc"] >= start) & (
        m["kickoff_utc"] < end)
    if competitions:
        mask &= m["competition"].isin(competitions)
    test = m.loc[mask].copy()
    test["local_date"] = test["kickoff_utc"].dt.tz_convert(UK).dt.date
    batches: list[Batch] = []
    for day, group in test.groupby("local_date", sort=True):
        if group["kickoff_time_known"].all():
            cutoff = group["kickoff_utc"].min() - pd.Timedelta(minutes=1)
        else:
            cutoff = pd.Timestamp(str(day), tz=UK).tz_convert("UTC")
        group = group.sort_values(["kickoff_utc", "fixture_id"])
        fixtures = tuple(
            FixtureSpec(str(fid), str(comp), str(home), str(away), ko.to_pydatetime())
            for fid, comp, home, away, ko in zip(
                group["fixture_id"], group["competition"], group["home_team"],
                group["away_team"], group["kickoff_utc"], strict=True)
        )
        actual = tuple((int(h), int(a)) for h, a in zip(group["home_goals"],
                                                        group["away_goals"], strict=True))
        batches.append(Batch(str(day), cutoff, fixtures, actual))
    return batches


def walk_forward(
    history: MatchHistory,
    models: Mapping[str, ScoreModel],
    rules: CompetitionRules,
    start: pd.Timestamp,
    end: pd.Timestamp,
    competitions: list[str] | None = None,
    refit_every_days: float = 1.0,
    progress: Callable[[str], Any] | None = None,
) -> pd.DataFrame:
    batches = make_batches(history, start, end, competitions)
    fitted: dict[str, FittedScoreModel] = {}
    refit = timedelta(days=refit_every_days)
    rows: list[dict[str, Any]] = []
    for b_index, batch in enumerate(batches):
        view = history.view(batch.cutoff)
        for label, model in models.items():
            current = fitted.get(label)
            if current is None or batch.cutoff - current.cutoff >= refit:
                # Cold fits (no warm start) so a replayed forecast is identical to a
                # live `super6 predict` at the same cutoff.
                current = model.fit(view)
                fitted[label] = current
            for fx, (hg, ag) in zip(batch.fixtures, batch.actual, strict=True):
                pred = current.predict(fx, view)
                dist = pred.distribution
                out = dist.outcome_probabilities()
                res = actual_result(hg, ag)
                jp, _ = choose_score(dist, Objective.JACKPOT, rules, 0)
                pp, _ = choose_score(dist, Objective.POINTS, rules, 0)
                rows.append({
                    "model": label,
                    "batch_id": batch.batch_id,
                    "cutoff": batch.cutoff,
                    "fixture_id": fx.fixture_id,
                    "competition": fx.competition,
                    "kickoff_utc": fx.kickoff_utc,
                    "home_team": fx.home_team,
                    "away_team": fx.away_team,
                    "home_goals": hg,
                    "away_goals": ag,
                    "lam_home": pred.lam_home,
                    "lam_away": pred.lam_away,
                    "p_home": out.home,
                    "p_draw": out.draw,
                    "p_away": out.away,
                    "p_actual_score": dist.prob(hg, ag),
                    "exact_log_loss": exact_log_loss(dist, hg, ag),
                    "outcome_log_loss": outcome_log_loss(out, res),
                    "rps": ranked_probability_score(out, res),
                    "brier": outcome_brier(out, res),
                    "jackpot_pick": f"{jp.home_goals}-{jp.away_goals}",
                    "jackpot_p_exact": jp.exact_probability,
                    "jackpot_points": super6_points(jp.home_goals, jp.away_goals, hg, ag, rules),
                    "points_pick": f"{pp.home_goals}-{pp.away_goals}",
                    "points_p_exact": pp.exact_probability,
                    "points_expected": pp.expected_points,
                    "points_points": super6_points(pp.home_goals, pp.away_goals, hg, ag, rules),
                    "warnings": ";".join(pred.warnings),
                })
        if progress and (b_index % 25 == 0 or b_index == len(batches) - 1):
            progress(f"{b_index + 1}/{len(batches)} match days (cutoff {batch.cutoff:%Y-%m-%d})")
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["jackpot_exact"] = frame["jackpot_points"] == rules.points_exact_score
        frame["points_exact"] = frame["points_points"] == rules.points_exact_score
        local = pd.to_datetime(frame["kickoff_utc"], utc=True).dt.tz_convert(UK)
        frame["season"] = np.where(local.dt.month >= 7, local.dt.year, local.dt.year - 1)
        frame["season"] = frame["season"].map(lambda y: f"{y}-{(y + 1) % 100:02d}")
    return frame


def synthetic_rounds(rows: pd.DataFrame, competitions: list[str], weekday: int,
                     local_kickoff: str, fixtures_per_round: int, seed: int) -> pd.DataFrame:
    """Score six-fixture batches drawn from Saturday games, labelled as **synthetic**.

    Genuine historical Super 6 fixture lists are not available, so these are *not*
    historical Super 6 rounds (DESIGN.md §13.2). Every model is scored on the same
    deterministic selection.
    """
    if rows.empty:
        return pd.DataFrame()
    first = rows.loc[rows["model"] == rows["model"].iloc[0]].copy()
    local = pd.to_datetime(first["kickoff_utc"], utc=True).dt.tz_convert(UK)
    eligible = first.loc[first["competition"].isin(competitions)
                         & (local.dt.day_of_week == weekday)
                         & (local.dt.strftime("%H:%M") == local_kickoff)]
    chosen: dict[str, list[str]] = {}
    for batch_id, group in eligible.groupby("batch_id", sort=True):
        ids = sorted(group["fixture_id"])
        if len(ids) < fixtures_per_round:
            continue
        rng = np.random.default_rng([seed, pd.Timestamp(str(batch_id)).toordinal()])
        chosen[str(batch_id)] = sorted(rng.choice(ids, fixtures_per_round, replace=False))
    if not chosen:
        return pd.DataFrame()
    pick = rows.loc[rows["fixture_id"].isin({f for ids in chosen.values() for f in ids})]
    out = pick.groupby(["model", "batch_id"]).agg(
        fixtures=("fixture_id", "size"),
        points_mode_points=("points_points", "sum"),
        jackpot_mode_points=("jackpot_points", "sum"),
        points_mode_exact=("points_exact", "sum"),
        jackpot_mode_exact=("jackpot_exact", "sum"),
        jackpot_joint_probability=("jackpot_p_exact", "prod"),
        season=("season", "first"),
    ).reset_index()
    out["jackpot_all_six"] = out["jackpot_mode_exact"] == fixtures_per_round
    out["synthetic"] = True
    return out
