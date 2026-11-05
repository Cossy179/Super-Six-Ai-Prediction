"""Residual score-dependence diagnostic (DESIGN.md §10.4: when is a bivariate model needed?).

Usage: python scripts/dependence_diagnostic.py reports/x/predictions.csv.gz [model ...]

For forecasts whose score grid is an independent Poisson in (λ_home, λ_away), checks:

* the residual covariance E[(H − λ_h)(A − λ_a)], which should be 0 under independence;
* observed vs forecast frequencies of 0-0, 1-1, 1-0, 0-1 and draws, with standard errors.

Rows that fell back to Dixon–Coles (non-zero ρ) are excluded, so the forecast grids are
exactly independent Poisson.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd
from scipy.stats import poisson


def diagnose(rows: pd.DataFrame) -> pd.DataFrame:
    lh, la = rows["lam_home"].to_numpy(), rows["lam_away"].to_numpy()
    h, a = rows["home_goals"].to_numpy(), rows["away_goals"].to_numpy()
    out = []
    cov = (h - lh) * (a - la)
    out.append(("residual covariance", 0.0, cov.mean(), cov.std(ddof=1) / np.sqrt(len(cov))))
    for hg, ag in ((0, 0), (1, 1), (1, 0), (0, 1)):
        p = poisson.pmf(hg, lh) * poisson.pmf(ag, la)
        hit = ((h == hg) & (a == ag)).astype(float)
        out.append((f"P({hg}-{ag})", p.mean(), hit.mean(),
                    np.sqrt(np.sum(p * (1 - p))) / len(p)))
    k = np.arange(16)
    pd_ = np.array([np.sum(poisson.pmf(k, x) * poisson.pmf(k, y)) for x, y in zip(lh, la,
                                                                                    strict=True)])
    draw = (h == a).astype(float)
    out.append(("P(draw)", pd_.mean(), draw.mean(), np.sqrt(np.sum(pd_ * (1 - pd_))) / len(pd_)))
    frame = pd.DataFrame(out, columns=["quantity", "forecast", "observed", "se"])
    frame["z"] = (frame["observed"] - frame["forecast"]) / frame["se"]
    return frame


def main() -> None:
    rows = pd.read_csv(sys.argv[1])
    models = sys.argv[2:] or ["market_poisson", "ensemble"]
    for model in models:
        m = rows.loc[rows["model"] == model]
        if "warnings" in m:
            m = m.loc[~m["warnings"].fillna("").str.contains("TEAM_MODEL_ONLY|FALLBACK")]
        if m.empty:
            continue
        print(f"\n{model}: {len(m)} fixtures with independent-Poisson grids")
        print(diagnose(m).to_string(index=False, float_format=lambda v: f"{v:.4f}"))


if __name__ == "__main__":
    main()
