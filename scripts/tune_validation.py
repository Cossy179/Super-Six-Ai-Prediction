"""Check Dixon–Coles hyperparameters on a VALIDATION window only.

Usage: python scripts/tune_validation.py --start 2022-07-01 --end 2023-07-01

Never point this at the test window you report: choosing settings after looking at test
results invalidates the test (DESIGN.md §13.1).
"""

from __future__ import annotations

import argparse
import itertools
import json

import pandas as pd

from super6.config import Settings
from super6.evaluation.replay import walk_forward
from super6.models import build_model
from super6.rules import load_rules
from super6.storage.db import make_engine, session_scope
from super6.storage.repositories import load_history


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--competitions", default="E0,E1,E2,E3,SC0")
    ap.add_argument("--xi", default="0.001,0.0019,0.003")
    ap.add_argument("--ridge", default="0.5,2,8")
    ap.add_argument("--refit-every-days", type=float, default=7)
    args = ap.parse_args()
    with session_scope(make_engine(Settings.from_env().database_url)) as s:
        history = load_history(s)
    rules = load_rules()
    models = {
        f"xi={xi},ridge={r}": build_model("dixon_coles",
                                          {"xi_per_day": float(xi), "ridge": float(r)})
        for xi, r in itertools.product(args.xi.split(","), args.ridge.split(","))
    }
    rows = walk_forward(history, models, rules, pd.Timestamp(args.start, tz="UTC"),
                        pd.Timestamp(args.end, tz="UTC"), args.competitions.split(","),
                        args.refit_every_days)
    table = rows.groupby("model").agg(
        fixtures=("fixture_id", "size"), exact_log_loss=("exact_log_loss", "mean"),
        rps=("rps", "mean"), points=("points_points", "mean"),
    ).sort_values("exact_log_loss")
    print(table.to_string(float_format=lambda v: f"{v:.4f}"))
    print(json.dumps({"window": [args.start, args.end], "best": table.index[0]}))


if __name__ == "__main__":
    main()
