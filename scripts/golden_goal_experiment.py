"""Experiment 006: uniform vs empirical goal-time profile for the Golden Goal.

Protocol: docs/experiments/2026-11-06-golden-goal-profile.md (committed before this ran).

Usage:
    python scripts/golden_goal_experiment.py \\
        --predictions reports/exp003-reused/predictions.csv.gz \\
        --start 2026-01-01 --end 2026-07-01 --out reports/exp006
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from super6.config import Settings, code_version
from super6.evaluation.metrics import paired_bootstrap
from super6.evaluation.tracking import log_experiment
from super6.features.point_in_time import MatchHistory
from super6.golden_goal.actual import first_goal_minutes, round_golden_goal
from super6.golden_goal.profile import GoalTimeProfile, profile_from_config, uniform_profile
from super6.golden_goal.simulate import GoldenGoalFixture, simulate_golden_goal
from super6.rules import CompetitionRules, load_rules
from super6.storage.db import make_engine, session_scope
from super6.storage.repositories import load_history

UK = "Europe/London"
CANDIDATE_CONFIG = {"profile": "empirical", "bin_minutes": 5,
                    "empirical_first_half_stoppage_cells": 3,
                    "empirical_second_half_stoppage_cells": 7}
# 21 categories of the first recorded goal minute (the last is "no goal").
BANDS = [*[(lo, lo + 4) for lo in range(1, 37, 5)], (41, 44), (45, 45),
         *[(lo, lo + 4) for lo in range(46, 82, 5)], (86, 89), (90, 90)]
LABELS = [f"{a}-{b}" if a != b else str(a) for a, b in BANDS] + ["none"]


def category(minute: int | None) -> int:
    if minute is None:
        return len(BANDS)
    for i, (a, b) in enumerate(BANDS):
        if a <= minute <= b:
            return i
    raise ValueError(f"minute {minute} outside 1..90")


def category_probs(profile: GoalTimeProfile, lam: float, mode: str) -> np.ndarray:
    mins, p_none = profile.first_goal_minutes(lam, mode)
    probs = np.array([mins[a:b + 1].sum() for a, b in BANDS] + [p_none])
    if abs(probs.sum() - 1.0) > 1e-9:
        raise AssertionError("first-goal categories do not sum to 1")
    return probs


class Profiles:
    """Walk-forward candidate profiles, cached by cutoff."""

    def __init__(self, history: MatchHistory) -> None:
        self.history = history
        self.cache: dict[pd.Timestamp, tuple[GoalTimeProfile, list[str]]] = {}

    def at(self, cutoff: pd.Timestamp) -> tuple[GoalTimeProfile, list[str]]:
        if cutoff not in self.cache:
            view = self.history.view(cutoff)
            self.cache[cutoff] = profile_from_config(CANDIDATE_CONFIG, view.goal_events)
        return self.cache[cutoff]


def week_of(ts: pd.Series) -> pd.Series:
    local = pd.to_datetime(ts, utc=True).dt.tz_convert(UK)
    iso = local.dt.isocalendar()
    return iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)


def match_level(preds: pd.DataFrame, first: dict[str, int | None], profiles: Profiles,
                baseline: GoalTimeProfile, mode: str) -> pd.DataFrame:
    rows = []
    for r in preds.itertuples():
        lam = float(r.lam_home + r.lam_away)
        cand, warn = profiles.at(pd.Timestamp(r.cutoff))
        cat = category(first[r.fixture_id])
        pb = category_probs(baseline, lam, mode)
        pc = category_probs(cand, lam, mode)
        rows.append({
            "fixture_id": r.fixture_id, "kickoff_utc": r.kickoff_utc, "cutoff": r.cutoff,
            "lam_total": lam, "first_minute": first[r.fixture_id], "category": LABELS[cat],
            "candidate_method": cand.method, "candidate_goals": cand.n_goals,
            "candidate_fallback": bool(warn),
            "ll_uniform": -float(np.log(pb[cat])), "ll_empirical": -float(np.log(pc[cat])),
            "p45_uniform": pb[LABELS.index("45")], "p45_empirical": pc[LABELS.index("45")],
            "p90_uniform": pb[LABELS.index("90")], "p90_empirical": pc[LABELS.index("90")],
        })
    out = pd.DataFrame(rows)
    out["week"] = week_of(out["kickoff_utc"])
    return out


def round_level(preds: pd.DataFrame, first: dict[str, int | None], profiles: Profiles,
                baseline: GoalTimeProfile, rules: CompetitionRules) -> pd.DataFrame:
    frame = preds.assign(week=week_of(preds["kickoff_utc"])).sort_values(
        ["kickoff_utc", "fixture_id"])
    rows = []
    for week, group in frame.groupby("week", sort=True):
        if len(group) < 6:
            continue
        six = group.head(6)
        start = pd.Timestamp(six["kickoff_utc"].min())
        cand, warn = profiles.at(start - pd.Timedelta(minutes=1))
        fixtures = [GoldenGoalFixture(r.fixture_id, pd.Timestamp(r.kickoff_utc).to_pydatetime(),
                                      float(r.lam_home + r.lam_away)) for r in six.itertuples()]
        actual = round_golden_goal({f: first[f] for f in six["fixture_id"]},
                                   rules.golden_goal_no_goal_minute)
        fb = simulate_golden_goal(fixtures, rules, profile=baseline)
        fc = simulate_golden_goal(fixtures, rules, profile=cand)
        rows.append({
            "week": week, "first_kickoff": start, "fixtures": ",".join(six["fixture_id"]),
            "actual": actual, "pick_uniform": fb.minute, "pick_empirical": fc.minute,
            "err_uniform": abs(fb.minute - actual), "err_empirical": abs(fc.minute - actual),
            "eae_uniform": fb.expected_abs_error, "eae_empirical": fc.expected_abs_error,
            "candidate_goals": cand.n_goals, "candidate_fallback": bool(warn),
        })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="ensemble")
    ap.add_argument("--competition", default="E0")
    ap.add_argument("--resamples", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rules = load_rules()
    mode = rules.golden_goal_stoppage_time
    with session_scope(make_engine(Settings.from_env().database_url)) as s:
        history = load_history(s, [args.competition])

    preds = pd.read_csv(args.predictions)
    for col in ("cutoff", "kickoff_utc"):
        preds[col] = pd.to_datetime(preds[col], utc=True)
    start, end = pd.Timestamp(args.start, tz="UTC"), pd.Timestamp(args.end, tz="UTC")
    preds = preds.loc[(preds["model"] == args.model) & (preds["competition"] == args.competition)
                      & (preds["kickoff_utc"] >= start) & (preds["kickoff_utc"] < end)]
    scores = {r.fixture_id: (int(r.home_goals), int(r.away_goals)) for r in preds.itertuples()}
    first = first_goal_minutes(history.goals, scores, mode)
    covered = preds.loc[preds["fixture_id"].isin(first.minutes)].copy()
    baseline = uniform_profile(2, 5)
    profiles = Profiles(history)

    matches = match_level(covered, first.minutes, profiles, baseline, mode)
    rounds = round_level(covered, first.minutes, profiles, baseline, rules)

    diff_a = matches["ll_empirical"] - matches["ll_uniform"]
    a = paired_bootstrap(diff_a, matches["week"], args.resamples, args.seed)
    diff_b = (rounds["err_empirical"] - rounds["err_uniform"]).astype(float)
    b = paired_bootstrap(diff_b, rounds["week"], args.resamples, args.seed)
    observed = matches["category"].value_counts(normalize=True)
    passes_a = a[0] < 0 and a[2] < 0
    passes_b = b[1] <= 0
    summary: dict[str, Any] = {
        "label": "exp006", "code_version": code_version(),
        "window": [args.start, args.end], "competition": args.competition,
        "predictions": args.predictions, "model": args.model,
        "test_matches": len(preds), "covered_matches": len(covered),
        "uncovered_matches": len(first.uncovered), "rounds": len(rounds),
        "candidate_fallbacks": int(matches["candidate_fallback"].sum()),
        "candidate_goals_range": [int(matches["candidate_goals"].min()),
                                  int(matches["candidate_goals"].max())],
        "a_first_goal_log_loss": {
            "uniform": float(matches["ll_uniform"].mean()),
            "empirical": float(matches["ll_empirical"].mean()),
            "diff_mean": a[0], "diff_lo": a[1], "diff_hi": a[2]},
        "b_golden_goal_abs_error": {
            "uniform": float(rounds["err_uniform"].mean()),
            "empirical": float(rounds["err_empirical"].mean()),
            "diff_mean": b[0], "diff_lo": b[1], "diff_hi": b[2],
            "expected_uniform": float(rounds["eae_uniform"].mean()),
            "expected_empirical": float(rounds["eae_empirical"].mean())},
        "stoppage_first_goal_share": {
            "observed_45": float(observed.get("45", 0.0)),
            "observed_90": float(observed.get("90", 0.0)),
            "uniform_45": float(matches["p45_uniform"].mean()),
            "empirical_45": float(matches["p45_empirical"].mean()),
            "uniform_90": float(matches["p90_uniform"].mean()),
            "empirical_90": float(matches["p90_empirical"].mean())},
        "condition_1": passes_a, "condition_2": passes_b,
        "decision": "adopt empirical" if passes_a and passes_b else "keep uniform",
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    matches.to_csv(out / "matches.csv", index=False)
    rounds.to_csv(out / "rounds.csv", index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    log_experiment({"id": f"exp006@{pd.Timestamp.now(tz='UTC'):%Y%m%dT%H%M%SZ}",
                    "label": "exp006", "window": [args.start, args.end],
                    "competitions": [args.competition], "code_version": code_version(),
                    "models": ["golden_goal:uniform", "golden_goal:empirical"],
                    "golden_goal": summary, "outputs": {"dir": str(out)}},
                   out.parent)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
