"""Backtest summaries: per-model and per-season tables, paired bootstrap comparisons,
calibration, and synthetic-round results, written as Markdown + CSV + JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from super6.evaluation.metrics import calibration_table, paired_bootstrap

METRICS = {
    "exact_log_loss": ("Exact-score log loss", "lower"),
    "rps": ("Ranked probability score", "lower"),
    "brier": ("Outcome Brier score", "lower"),
    "points_points": ("Points/fixture (points mode)", "higher"),
    "jackpot_points": ("Points/fixture (jackpot mode)", "higher"),
    "jackpot_exact": ("Exact-score hit rate (jackpot mode)", "higher"),
}


def summarise(rows: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    agg = rows.groupby(by).agg(
        fixtures=("fixture_id", "size"),
        exact_log_loss=("exact_log_loss", "mean"),
        rps=("rps", "mean"),
        brier=("brier", "mean"),
        outcome_log_loss=("outcome_log_loss", "mean"),
        jackpot_exact=("jackpot_exact", "mean"),
        points_exact=("points_exact", "mean"),
        jackpot_points=("jackpot_points", "mean"),
        points_points=("points_points", "mean"),
        points_expected=("points_expected", "mean"),
        flagged=("warnings", lambda w: float((w != "").mean())),
    )
    return agg.reset_index()


def compare(rows: pd.DataFrame, reference: str, resamples: int, seed: int) -> pd.DataFrame:
    """Reference minus each other model, per metric, with block-bootstrap 95% CIs."""
    out = []
    ref = rows.loc[rows["model"] == reference].set_index("fixture_id")
    for model in sorted(set(rows["model"]) - {reference}):
        other = rows.loc[rows["model"] == model].set_index("fixture_id")
        common = ref.index.intersection(other.index)
        for metric, (label, better) in METRICS.items():
            diff = ref.loc[common, metric].astype(float) - other.loc[common, metric].astype(float)
            mean, lo, hi = paired_bootstrap(diff, ref.loc[common, "batch_id"], resamples, seed)
            significant = (lo > 0 or hi < 0)
            ref_better = (mean < 0) if better == "lower" else (mean > 0)
            out.append({"reference": reference, "versus": model, "metric": metric,
                        "label": label, "better": better, "mean_diff": mean, "ci_low": lo,
                        "ci_high": hi, "fixtures": len(common),
                        "verdict": ("reference better" if ref_better else "reference worse")
                        if significant else "no clear difference"})
    return pd.DataFrame(out)


def outcome_calibration(rows: pd.DataFrame, model: str) -> pd.DataFrame:
    m = rows.loc[rows["model"] == model]
    probs = np.concatenate([m["p_home"], m["p_draw"], m["p_away"]])
    hits = np.concatenate([m["home_goals"] > m["away_goals"], m["home_goals"] == m["away_goals"],
                           m["home_goals"] < m["away_goals"]])
    return calibration_table(probs, hits)


def exact_calibration(rows: pd.DataFrame, model: str) -> pd.DataFrame:
    m = rows.loc[rows["model"] == model]
    return calibration_table(m["jackpot_p_exact"], m["jackpot_exact"],
                             bins=(0, .06, .08, .10, .12, .14, .16, .20, 1.0))


@dataclass
class BacktestReport:
    markdown: str
    summary: dict[str, Any]


def _fmt_table(df: pd.DataFrame, floats: int = 4) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |",
             "|" + "|".join("---" if df[c].dtype == object else "---:" for c in cols) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, float | np.floating):
                cells.append(f"{v:.{floats}f}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_report(rows: pd.DataFrame, rounds: pd.DataFrame, *, reference: str,
                 meta: dict[str, Any], resamples: int = 2000, seed: int = 7) -> BacktestReport:
    overall = summarise(rows, ["model"]).sort_values("exact_log_loss")
    by_season = summarise(rows, ["season", "model"]).sort_values(["season", "exact_log_loss"])
    comparisons = compare(rows, reference, resamples, seed)
    out_cal = outcome_calibration(rows, reference)
    ex_cal = exact_calibration(rows, reference)
    md = [
        f"# Walk-forward backtest — {meta['label']}",
        "",
        f"- Test window: **{meta['start']} → {meta['end']}** (UTC), competitions: "
        f"{', '.join(meta['competitions']) or 'all'}",
        f"- Fixtures per model: **{int(overall['fixtures'].iloc[0])}** across "
        f"{rows['batch_id'].nunique()} match days; refit every {meta['refit_every_days']} day(s)",
        f"- Rules: `{meta['rules_version']}` (verified: {meta['rules_verified']}); "
        f"code `{meta['code_version']}`",
        "- Each cutoff is one minute before that day's first kick-off; models only see "
        "results published and odds collected before it.",
        "- Hyperparameters are the documented defaults in `configs/model_defaults.yaml`; "
        "none were tuned on this window.",
        "",
        "## Overall",
        "",
        _fmt_table(overall),
        "",
        f"## Paired comparison: `{reference}` minus each model",
        "",
        "95% intervals from a block bootstrap over match days. For log loss, RPS and Brier a "
        "negative difference favours the reference; for points and hit rate a positive one does.",
        "",
        _fmt_table(comparisons[["versus", "label", "mean_diff", "ci_low", "ci_high",
                                "verdict"]]) if not comparisons.empty else "_single model_",
        "",
        "## By season (fold-by-fold)",
        "",
        _fmt_table(by_season),
        "",
        f"## Calibration — `{reference}` home/draw/away probabilities",
        "",
        _fmt_table(out_cal.astype({"bin": str})),
        "",
        f"## Calibration — `{reference}` probability of the jackpot pick",
        "",
        _fmt_table(ex_cal.astype({"bin": str})),
        "",
    ]
    rounds_summary: list[dict[Any, Any]] = []
    if not rounds.empty:
        rs = rounds.groupby("model").agg(
            rounds=("batch_id", "size"),
            points_mode_mean=("points_mode_points", "mean"),
            jackpot_mode_mean=("jackpot_mode_points", "mean"),
            points_mode_exact_mean=("points_mode_exact", "mean"),
            jackpot_mode_exact_mean=("jackpot_mode_exact", "mean"),
            all_six_exact=("jackpot_all_six", "sum"),
            median_joint_probability=("jackpot_joint_probability", "median"),
        ).reset_index().sort_values("points_mode_mean", ascending=False)
        rounds_summary = rs.to_dict(orient="records")
        md += [
            "## Synthetic six-fixture rounds (NOT historical Super 6 rounds)",
            "",
            "Six Saturday 15:00 fixtures per week drawn deterministically from "
            f"{', '.join(meta['synthetic_competitions'])}; the same fixtures for every model. "
            "Real Super 6 round lists are not available, so round-level numbers are "
            "illustrative of match-level skill only.",
            "",
            _fmt_table(rs),
            "",
        ]
    md += [
        "## Reading these numbers",
        "",
        "- Exact scores are hard: even a good model's most likely score usually has a "
        "10–15% chance. Six exact scores together are far less likely.",
        "- A model counts as better only when the interval excludes zero, and the gain "
        "repeats across seasons.",
        "",
    ]
    summary = {
        "meta": meta,
        "overall": overall.to_dict(orient="records"),
        "by_season": by_season.to_dict(orient="records"),
        "comparisons": comparisons.to_dict(orient="records"),
        "synthetic_rounds": rounds_summary,
    }
    return BacktestReport("\n".join(md), summary)


def write_report(report: BacktestReport, rows: pd.DataFrame, rounds: pd.DataFrame,
                 out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "markdown": out_dir / "report.md",
        "summary": out_dir / "summary.json",
        "predictions": out_dir / "predictions.csv.gz",
        "rounds": out_dir / "synthetic_rounds.csv",
    }
    paths["markdown"].write_text(report.markdown, encoding="utf-8")
    paths["summary"].write_text(json.dumps(report.summary, indent=2, default=str))
    rows.to_csv(paths["predictions"], index=False)
    rounds.to_csv(paths["rounds"], index=False)
    return paths
