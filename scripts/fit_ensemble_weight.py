"""Fit the ensemble's market weight on a VALIDATION window.

Usage: python scripts/fit_ensemble_weight.py --start 2022-07-01 --end 2023-07-01

Runs a walk-forward replay of the team model and the market model, then picks the single
weight w minimising mean exact-score log loss of the mixture
(1 - w) * P_team + w * P_market over fixtures where pre-cutoff odds existed. Because each
component's probability of the actual score is stored per fixture, the mixture's log
loss for any w needs no refitting. Never run this on the window you report as a test.
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd
from scipy.stats import poisson

from super6.config import Settings
from super6.evaluation.replay import walk_forward
from super6.models import build_model
from super6.rules import load_rules
from super6.storage.db import make_engine, session_scope
from super6.storage.repositories import load_history


def mixture_log_loss(p_team: np.ndarray, p_market: np.ndarray, w: float) -> float:
    return float(np.mean(-np.log(np.maximum((1 - w) * p_team + w * p_market, 1e-15))))


def log_rate_log_loss(rows: pd.DataFrame, w: float) -> float:
    """Alternative combiner: blend log goal rates, then score with independent Poisson."""
    lam_h = np.exp((1 - w) * np.log(rows["team_lh"]) + w * np.log(rows["market_lh"]))
    lam_a = np.exp((1 - w) * np.log(rows["team_la"]) + w * np.log(rows["market_la"]))
    p = poisson.pmf(rows["home_goals"], lam_h) * poisson.pmf(rows["away_goals"], lam_a)
    return float(np.mean(-np.log(np.maximum(p, 1e-15))))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--competitions", default="E0,E1,E2,E3,SC0")
    ap.add_argument("--team-model", default="dixon_coles")
    ap.add_argument("--out", default=None, help="Optional CSV of the weight curve")
    args = ap.parse_args()
    with session_scope(make_engine(Settings.from_env().database_url)) as s:
        history = load_history(s)
    models = {"team": build_model(args.team_model), "market": build_model("market_poisson")}
    rows = walk_forward(history, models, load_rules(), pd.Timestamp(args.start, tz="UTC"),
                        pd.Timestamp(args.end, tz="UTC"), args.competitions.split(","))
    # pivot (not pivot_table): one row per (fixture, model); duplicates must raise.
    wide = rows.pivot(index="fixture_id", columns="model", values="p_actual_score")  # noqa: PD010
    fallback = rows.loc[(rows["model"] == "market")
                        & rows["warnings"].str.contains("MARKET_ODDS_UNAVAILABLE"), "fixture_id"]
    usable = wide.drop(index=fallback)
    p_t, p_m = usable["team"].to_numpy(), usable["market"].to_numpy()
    rates = rows.pivot(  # noqa: PD010
        index="fixture_id", columns="model", values=["lam_home", "lam_away"])
    goals = rows.loc[rows["model"] == "team"].set_index("fixture_id")[["home_goals",
                                                                       "away_goals"]]
    lr = pd.DataFrame({"team_lh": rates[("lam_home", "team")],
                       "market_lh": rates[("lam_home", "market")],
                       "team_la": rates[("lam_away", "team")],
                       "market_la": rates[("lam_away", "market")]}).join(goals)
    lr = lr.loc[usable.index]
    grid = np.round(np.arange(0, 1.0001, 0.01), 2)
    curve = pd.DataFrame({
        "market_weight": grid,
        "exact_log_loss": [mixture_log_loss(p_t, p_m, w) for w in grid],
        "log_rate_blend_log_loss": [log_rate_log_loss(lr, w) for w in grid],
    })
    best = curve.loc[curve["exact_log_loss"].idxmin()]
    if args.out:
        curve.to_csv(args.out, index=False)
    print(curve.iloc[::10].to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print(json.dumps({
        "window": [args.start, args.end],
        "fixtures_used": len(usable),
        "fixtures_without_odds": int(fallback.nunique()),
        "best_market_weight": float(best["market_weight"]),
        "log_loss_team_only": mixture_log_loss(p_t, p_m, 0.0),
        "log_loss_market_only": mixture_log_loss(p_t, p_m, 1.0),
        "log_loss_best": float(best["exact_log_loss"]),
        "log_rate_blend_best_weight": float(
            curve.loc[curve["log_rate_blend_log_loss"].idxmin(), "market_weight"]),
        "log_rate_blend_best_log_loss": float(curve["log_rate_blend_log_loss"].min()),
    }, indent=2))


if __name__ == "__main__":
    main()
