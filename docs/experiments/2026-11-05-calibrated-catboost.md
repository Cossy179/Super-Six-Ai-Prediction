# Experiment 004: recalibrated CatBoost challengers

- **Date:** 5 November 2026
- **Status:** protocol and promotion rules committed *before* any result was seen.
  Results are added below once the runs finish.

## Motivation

Experiment 003 found both CatBoost challengers overconfident: expected points exceeded
realised points by 0.03–0.05 per fixture. `models/calibration.py` adds a leak-free
per-side Poisson recalibration `log λ' = a + s · log λ`. At every refit it is fitted on
genuinely out-of-sample forecasts from the previous 182 days, made by the base model as
it stood at the start of those 182 days. Spot checks found slopes of 0.55–0.93, i.e.
shrinkage, as expected.

The idea for this model came from experiment 003's results on the 2019–22 and 2023–26
windows. Those windows are therefore consistency checks here, not tests.

## Candidates

`ensemble` (champion), `dixon_coles` (current fallback), `catboost`, `catboost_cal`,
`catboost_market`, `catboost_market_cal`. CatBoost settings are unchanged from
experiment 003; calibration settings are `calibration_days: 182` and
`min_calibration_fixtures: 300`, untuned.

## Windows

- **Fresh test: 2018-07-01 → 2019-07-01.** The only season never used for any decision.
  The source has no kick-off times for it, so cutoffs fall at local midnight, and some
  midweek fixtures will have no usable pre-match odds (the ensemble falls back to
  Dixon–Coles for them). CatBoost has two seasons of training history at the start.
- **Reused: 2019-07-01 → 2022-07-01 and 2023-07-01 → 2026-11-05.** Only the calibrated
  models are run. The other models' forecasts are merged from experiment 003
  (`--merge`), which checks that every merged model covers exactly the same fixtures.
  Their code is unchanged since that run.

## Promotion rules (declared in advance)

**R1, new default.** `catboost_market_cal` replaces `ensemble` only if all hold:

1. On the fresh window, its exact-score log loss beats `ensemble`, with the 95% interval
   excluding zero.
2. On the fresh window, its points per fixture (points mode) is not significantly worse.
3. On *both* reused windows, its log loss is not significantly worse than `ensemble`.

**R2, new fallback** (if R1 fails). `catboost_cal` replaces `dixon_coles` as the
ensemble's `team_model` only if the same three conditions hold against `dixon_coles`.

**R3, descriptive only.** Report whether calibration reduced the expected-vs-realised
points gap, and the log-loss change `catboost_cal − catboost` and
`catboost_market_cal − catboost_market`, on every window.

## Commands

```bash
python scripts/run_experiment.py --start 2018-07-01 --end 2019-07-01 --reference ensemble \
  --label exp004-fresh --out reports/exp004-fresh \
  --model ensemble=ensemble --model dixon_coles=dixon_coles --model catboost=catboost \
  --model catboost_cal=catboost_cal --model catboost_market=catboost_market \
  --model catboost_market_cal=catboost_market_cal
python scripts/run_experiment.py --start 2019-07-01 --end 2022-07-01 --reference ensemble \
  --label exp004-reused-2019 --out reports/exp004-reused-2019 \
  --model catboost_cal=catboost_cal --model catboost_market_cal=catboost_market_cal \
  --merge reports/exp003-fresh/predictions.csv.gz \
  --merge-models ensemble,dixon_coles,catboost,catboost_market
python scripts/run_experiment.py --start 2023-07-01 --end 2026-11-05 --reference ensemble \
  --label exp004-reused-2023 --out reports/exp004-reused-2023 \
  --model catboost_cal=catboost_cal --model catboost_market_cal=catboost_market_cal \
  --merge reports/exp003-reused/predictions.csv.gz \
  --merge-models ensemble,dixon_coles,catboost,catboost_market
```
