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

## Results (added after the run)

Run at code `cb28f55`; generated output in
[`2026-11-05-ensemble-generated-report.md`](2026-11-05-ensemble-generated-report.md).
7,441 fixtures. 2.7% had no pre-cutoff odds and used Dixon–Coles.

| Model | Exact-score log loss ↓ | RPS ↓ | Exact hits (jackpot) | Points/fixture (points mode) |
|---|---:|---:|---:|---:|
| `ensemble` | **2.8810** | **0.2095** | 13.0% | **1.326** |
| `market_poisson` | 2.8818 | 0.2098 | 12.9% | 1.323 |
| `dixon_coles` | 2.9071 | 0.2138 | 12.7% | 1.299 |

Ensemble minus Dixon–Coles (95% block-bootstrap intervals):

- exact-score log loss −0.0261 (−0.0318 to −0.0209): **better**;
- points per fixture, points mode +0.0276 (+0.0022 to +0.0533): **better**, which more
  than satisfies "not significantly worse";
- the ensemble is ahead in every season on log loss, and in three of four on points mode.

Against `market_poisson` the differences are small, as expected. They come only from the
fixtures without odds, where Dixon–Coles is a better fallback than the league average.
RPS and Brier differences favour the ensemble, and log loss is level within noise.

**Decision:** both predeclared conditions hold, so `ensemble` is now `default_model`.

## Caveats

- This is a second use of the 2023–26 window. The weight was fixed on 2022-23 beforehand,
  and the rule was committed before the run. The live shadow period is still the real
  test.
- The gain depends on having pre-deadline odds. In live use, run `super6 data upcoming`
  after the provider publishes the weekend's fixtures (usually by Friday), or enter odds
  in the round file. Fixtures without odds fall back to Dixon–Coles and are shown as
  `results model` in the `basis` column.
- With w = 1 the team model contributes only when odds are missing. Better use of team
  information (xG, line-ups) is the next candidate for adding value beyond the market.
