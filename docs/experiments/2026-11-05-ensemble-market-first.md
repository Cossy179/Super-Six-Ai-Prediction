# Experiment 002: market-first ensemble with team-model fallback

- **Date:** 5 November 2026
- **Status:** protocol declared and committed *before* the test run. Results are added below.

## Question

Experiment 001 found that de-vigged pre-match odds forecast better than Dixon–Coles. The
market-only model falls back to the league-average baseline when no odds were available
before the cutoff. Does combining the market with the team-strength model improve on both?

## Weight fitting (validation season 2022-23 only)

`python scripts/fit_ensemble_weight.py --start 2022-07-01 --end 2023-07-01`
([output](2026-11-05-ensemble-weight-2022-23.txt), [curve](2026-11-05-ensemble-weight-curve-2022-23.csv)):
2,224 fixtures with pre-cutoff odds, plus 40 without.

| Market weight | Mixture log loss | Log-rate blend log loss |
|---:|---:|---:|
| 0.0 (Dixon–Coles only) | 2.8629 | 2.8628 |
| 0.5 | 2.8413 | 2.8419 |
| 1.0 (market only) | **2.8327** | **2.8327** |

Log loss falls steadily as the weight rises, under both combiners. Once odds exist, the
results-only model adds no measurable information. The candidate is therefore
`ensemble` with `market_weight = 1.0`: the market forecast when pre-cutoff odds exist,
and Dixon–Coles (flagged `ENSEMBLE_TEAM_MODEL_ONLY`) when they don't.

## Predeclared test and promotion rule

- **Window:** 2023-07-01 → 2026-11-05, competitions E0, E1, E2, E3, SC0, daily refits.
  This is the same window as experiment 001, so it is a *second* use of that window.
  The weight above was fixed on 2022-23 before this run, and the live shadow period
  remains the final check (DESIGN §13.5).
- **Command:** `super6 backtest --start 2023-07-01 --end 2026-11-05 --models ensemble,dixon_coles,market_poisson --reference ensemble`
- **Promote `ensemble` to the default model if both hold:**
  1. its exact-score log loss beats `dixon_coles`, with the 95% block-bootstrap interval
     excluding zero; and
  2. its points per fixture (points mode) is not significantly worse than `dixon_coles`
     (the interval for ensemble minus Dixon–Coles does not lie entirely below zero).
- **Otherwise** keep `dixon_coles` as the default and report the result.
