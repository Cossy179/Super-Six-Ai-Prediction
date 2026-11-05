"""Paired block-bootstrap comparisons between any two models in a predictions file.

Usage: python scripts/compare_pairs.py reports/x/predictions.csv.gz catboost:dixon_coles ...
Each pair is A:B and reports A minus B.
"""

from __future__ import annotations

import sys

import pandas as pd

from super6.evaluation.metrics import paired_bootstrap

METRICS = ["exact_log_loss", "rps", "points_points", "jackpot_points"]


def main() -> None:
    rows = pd.read_csv(sys.argv[1])
    for pair in sys.argv[2:]:
        a, b = pair.split(":")
        ra = rows.loc[rows["model"] == a].set_index("fixture_id")
        rb = rows.loc[rows["model"] == b].set_index("fixture_id")
        common = ra.index.intersection(rb.index)
        for metric in METRICS:
            diff = ra.loc[common, metric].astype(float) - rb.loc[common, metric].astype(float)
            mean, lo, hi = paired_bootstrap(diff, ra.loc[common, "batch_id"], 2000, 7)
            print(f"{a} - {b}  {metric:15s} {mean:+.4f}  [{lo:+.4f}, {hi:+.4f}]")


if __name__ == "__main__":
    main()
