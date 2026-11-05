# Experiment 003: feature families and CatBoost challengers

- **Date:** 5 November 2026
- **Status:** protocol and promotion rules committed *before* any result was seen.
  Results are added below once the runs finish.

## Candidates

| Label | Model | Inputs |
|---|---|---|
| `ensemble` | champion (market odds when available, else Dixon–Coles) | odds, results |
| `dixon_coles` | current results-only fallback | results |
| `catboost` | CatBoost Poisson goal rates | Elo, goals, shots, xG, schedule, tier |
| `catboost_no_shots` | ablation | Elo, goals, schedule, tier |
| `catboost_elo_goals` | ablation | Elo, goals |
| `catboost_market` | CatBoost with market inputs | all of the above + de-vigged pre-match odds |

CatBoost settings are untuned, typical library values declared in
`configs/model_defaults.yaml`. CatBoost models refit every 28 days; their team features
update at every cutoff. Everything else refits daily.

## Windows

- **Fresh test: 2019-07-01 → 2022-07-01.** Not used for any earlier choice. It includes
  the COVID seasons, when matches were played without crowds and home advantage
  collapsed, so it is a stress test. Caveat: the ensemble weight and the Dixon–Coles
  settings were fixed on 2022-23, a later season. That can only flatter the *champion*,
  so it makes the test conservative for the challengers.
- **Reused window: 2023-07-01 → 2026-11-05.** Already used in experiments 001–002, so it
  is a consistency check, not a selection set.
- Competitions E0, E1, E2, E3, SC0. Paired block bootstrap over match days, 95% intervals.

## Promotion rules (declared in advance)

**R1, new default.** `catboost_market` replaces `ensemble` as `default_model` only if all
of the following hold:

1. On the fresh window, its exact-score log loss beats `ensemble`, with the interval
   excluding zero.
2. On the fresh window, its points per fixture (points mode) is not significantly worse
   than `ensemble`.
3. On the reused window, its log loss is not significantly worse than `ensemble`.

**R2, new fallback** (applies only if R1 fails). `catboost` replaces `dixon_coles` as the
ensemble's `team_model` only if all of the following hold:

1. On the fresh window, its log loss beats `dixon_coles`, with the interval excluding
   zero.
2. On the fresh window, its points per fixture is not significantly worse than
   `dixon_coles`.
3. On the reused window, its log loss is not significantly worse than `dixon_coles`.

**R3, feature families** (DESIGN §9.2). Shots/shots-on-target stay in the default
CatBoost features only if `catboost` beats `catboost_no_shots` on fresh-window log loss,
with the interval excluding zero. xG cannot be evaluated: no season before 2026-27 has it.
It stays as an optional, explicitly-missing input and is reported as untested.

## Commands

```bash
python scripts/run_experiment.py --start 2019-07-01 --end 2022-07-01 --reference ensemble \
  --label exp003-fresh --out reports/exp003-fresh \
  --model ensemble=ensemble --model dixon_coles=dixon_coles --model catboost=catboost \
  --model catboost_no_shots=catboost:families=elo,goals,schedule,tier \
  --model catboost_elo_goals=catboost:families=elo,goals \
  --model catboost_market=catboost_market
python scripts/run_experiment.py --start 2023-07-01 --end 2026-11-05 --reference ensemble \
  --label exp003-reused --out reports/exp003-reused \
  --model ensemble=ensemble --model dixon_coles=dixon_coles --model catboost=catboost \
  --model catboost_market=catboost_market
```

## Results (added after both runs)

Both runs used code `68730ba`, the commit that recorded this protocol. Generated output:
[fresh](2026-11-05-catboost-fresh-generated-report.md),
[reused](2026-11-05-catboost-reused-generated-report.md). Pairwise intervals not shown in
those reports come from `scripts/compare_pairs.py`, which uses the same block bootstrap.

### Fresh window, 2019-07 → 2022-07 (6,479 fixtures)

| Model | Exact-score log loss ↓ | RPS ↓ | Points/fixture (points mode) |
|---|---:|---:|---:|
| `ensemble` (champion) | **2.8368** | **0.2102** | **1.341** |
| `catboost_market` | 2.8518 | 0.2123 | 1.296 |
| `dixon_coles` | 2.8656 | 0.2149 | 1.286 |
| `catboost` | 2.8673 | 0.2160 | 1.283 |
| `catboost_no_shots` | 2.8735 | 0.2170 | 1.270 |
| `catboost_elo_goals` | 2.8748 | 0.2171 | 1.266 |

The ensemble ranks first on log loss in each of the three seasons.

### Reused window, 2023-07 → 2026-11 (7,441 fixtures)

| Model | Exact-score log loss ↓ | Points/fixture (points mode) |
|---|---:|---:|
| `ensemble` | **2.8810** | **1.326** |
| `catboost_market` | 2.9071 | 1.314 |
| `dixon_coles` | 2.9071 | 1.299 |
| `catboost` | 2.9216 | 1.295 |

### Decisions against the predeclared rules

| Rule | Evidence (A minus B, 95% interval) | Outcome |
|---|---|---|
| R1: `catboost_market` replaces `ensemble` | Fresh log loss +0.0149 [+0.0100, +0.0200]; points −0.044 [−0.069, −0.021] | **Fails.** `ensemble` stays the default |
| R2: `catboost` replaces `dixon_coles` as fallback | Fresh log loss +0.0017 [−0.0056, +0.0089]; reused +0.0144 [+0.0050, +0.0248] | **Fails.** Dixon–Coles stays the fallback |
| R3: shots stay in CatBoost features | `catboost` − `catboost_no_shots`, fresh log loss −0.0062 [−0.0118, −0.0004] | **Passes**, narrowly |

No configuration changes follow from this experiment, because neither challenger was
promoted.

### What we learned

1. **The market is still the hardest benchmark.** Giving CatBoost the market
   probabilities *plus* team features did worse than using the market directly. The
   learned model adds noise faster than it adds information.
2. **CatBoost is overconfident.** Its expected points exceed realised points by
   0.034–0.049 per fixture. For comparison, Dixon–Coles's gap is 0.023 (fresh) and 0.003
   (reused), and the ensemble's is −0.020 and −0.006. CatBoost's goal rates are too
   extreme, which is a calibration problem, not a lack of signal.
3. **Shots on target carry real signal, but only a little.** They are the only feature
   family that measurably helped. Schedule and tier features made no detectable
   difference (`catboost_no_shots` vs `catboost_elo_goals`: −0.0013 [−0.0045, +0.0019]).
4. **xG remains untested.** There is less than one season of history in this source.

### Exploratory, not predeclared, so weak evidence

CatBoost models scored more points than Dixon–Coles in *jackpot* mode on the fresh window
(`catboost` +0.068 [+0.031, +0.106] points/fixture). This was one of many comparisons, and
jackpot mode is not the default, so treat it as a lead to re-test, not a result.

## Next steps suggested by this experiment

- Calibrate the challenger before testing it again, for example by shrinking predicted
  log-rates towards the competition mean, with the shrinkage fitted on a validation
  season. Then re-test under a new predeclared rule.
- Revisit xG once a full season (2026-27) is available.
- New information sources (line-ups, injuries) are the realistic route to beating the
  market. A results-based model can't add much to it.
