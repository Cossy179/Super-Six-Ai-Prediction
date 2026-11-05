"""Walk-forward experiment with labelled model variants (ablations).

Usage:
  python scripts/run_experiment.py --start 2019-07-01 --end 2022-07-01 \
      --model ensemble=ensemble --model cb=catboost \
      --model cb_no_shots=catboost:families=elo,goals,schedule,tier \
      --reference ensemble --label my-run --out reports/my-run

Each ``--model`` is ``label=registry_name`` with optional ``:key=value`` overrides
(comma-separated values become lists).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

from super6.config import Settings, code_version
from super6.evaluation.replay import synthetic_rounds, walk_forward
from super6.evaluation.reports import build_report, write_report
from super6.evaluation.tracking import experiment_record, log_experiment
from super6.models import build_model, load_model_defaults
from super6.rules import load_rules
from super6.storage.db import make_engine, session_scope
from super6.storage.repositories import load_history


def parse_model(spec: str) -> tuple[str, str, dict[str, Any]]:
    label, rest = spec.split("=", 1)
    name, *opts = rest.split(":")
    overrides: dict[str, Any] = {}
    for opt in opts:
        key, value = opt.split("=", 1)
        overrides[key] = value.split(",") if "," in value else value
    return label, name, overrides


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--competitions", default="E0,E1,E2,E3,SC0")
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--merge", default=None,
                    help="predictions.csv.gz from an earlier run whose models are unchanged")
    ap.add_argument("--merge-models", default="",
                    help="comma-separated models to take from --merge")
    args = ap.parse_args()
    defaults = load_model_defaults()
    models, specs = {}, {}
    for spec in args.model:
        label, name, overrides = parse_model(spec)
        models[label] = build_model(name, overrides, defaults)
        specs[label] = {"model": name, "overrides": overrides}
    with session_scope(make_engine(Settings.from_env().database_url)) as s:
        history = load_history(s)
    rules = load_rules()
    comps = args.competitions.split(",")
    rows = walk_forward(history, models, rules, pd.Timestamp(args.start, tz="UTC"),
                        pd.Timestamp(args.end, tz="UTC"), comps,
                        defaults["backtest"]["refit_every_days"],
                        progress=lambda m: print(m, flush=True))
    if args.merge:
        old = pd.read_csv(args.merge)
        old = old.loc[old["model"].isin(args.merge_models.split(","))].copy()
        old["warnings"] = old["warnings"].fillna("")
        for col in ("cutoff", "kickoff_utc"):
            old[col] = pd.to_datetime(old[col], utc=True)
            rows[col] = pd.to_datetime(rows[col], utc=True)
        new_ids = set(rows["fixture_id"])
        for model, group in old.groupby("model"):
            if set(group["fixture_id"]) != new_ids:
                raise SystemExit(f"--merge: {model} covers different fixtures; rerun it instead")
        rows = pd.concat([rows, old[rows.columns]], ignore_index=True)
        for label in sorted(set(old["model"])):
            specs[label] = {"model": label, "merged_from": args.merge}
    sr = defaults["backtest"]["synthetic_rounds"]
    rounds = synthetic_rounds(rows, sr["competitions"], sr["weekday"], sr["local_kickoff"],
                              sr["fixtures_per_round"], sr["seed"])
    meta = {"label": args.label, "start": args.start, "end": args.end, "competitions": comps,
            "models": specs, "reference": args.reference,
            "refit_every_days": {k: max(defaults["backtest"]["refit_every_days"],
                                        m.refit_every_days or 0) for k, m in models.items()},
            "rules_version": rules.rules_version, "rules_verified": rules.verified,
            "code_version": code_version(), "synthetic_competitions": sr["competitions"]}
    boot = defaults["backtest"]["bootstrap"]
    report = build_report(rows, rounds, reference=args.reference, meta=meta,
                          resamples=boot["resamples"], seed=boot["seed"])
    paths = write_report(report, rows, rounds, Path(args.out))
    log = log_experiment(experiment_record(meta, report.summary, paths),
                         Settings.from_env().reports_dir)
    print(f"wrote {paths['markdown']} (logged in {log})", flush=True)


if __name__ == "__main__":
    main()
