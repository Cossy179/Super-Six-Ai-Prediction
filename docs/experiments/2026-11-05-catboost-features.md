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
