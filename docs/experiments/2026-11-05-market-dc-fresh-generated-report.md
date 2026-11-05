# Walk-forward backtest — exp005-fresh

- Test window: **2018-07-01 → 2019-07-01** (UTC), competitions: E0, E1, E2, E3, SC0
- Fixtures per model: **2264** across 193 match days; refit every {'ensemble_dc': 1} day(s)
- Rules: `sky-terms-2026-27` (verified: True); code `0.1.0+g6b35703ca8b3-dirty`
- Each cutoff is one minute before that day's first kick-off; models only see results published and odds collected before it.
- Hyperparameters are the documented defaults in `configs/model_defaults.yaml`; check that none were chosen using this window.

## Overall

| model | fixtures | exact_log_loss | rps | brier | outcome_log_loss | jackpot_exact | points_exact | jackpot_points | points_points | points_expected | flagged |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ensemble | 2264 | 2.8549 | 0.2062 | 0.6003 | 1.0031 | 0.1241 | 0.1064 | 1.1444 | 1.3167 | 1.3226 | 0.1661 |
| ensemble_dc | 2264 | 2.8553 | 0.2061 | 0.6002 | 1.0028 | 0.1215 | 0.1051 | 1.0570 | 1.2986 | 1.2904 | 0.1661 |
| dixon_coles | 2264 | 2.8737 | 0.2089 | 0.6058 | 1.0112 | 0.1259 | 0.1016 | 1.1170 | 1.2862 | 1.3093 | 0.0000 |

## Paired comparison: `ensemble_dc` minus each model

95% intervals from a block bootstrap over match days. For log loss, RPS and Brier a negative difference favours the reference; for points and hit rate a positive one does.

| versus | label | mean_diff | ci_low | ci_high | verdict |
|---:|---:|---:|---:|---:|---:|
| dixon_coles | Exact-score log loss | -0.0184 | -0.0259 | -0.0109 | reference better |
| dixon_coles | Ranked probability score | -0.0028 | -0.0045 | -0.0011 | reference better |
| dixon_coles | Outcome Brier score | -0.0056 | -0.0092 | -0.0020 | reference better |
| dixon_coles | Points/fixture (points mode) | 0.0124 | -0.0308 | 0.0582 | no clear difference |
| dixon_coles | Points/fixture (jackpot mode) | -0.0601 | -0.1037 | -0.0123 | reference worse |
| dixon_coles | Exact-score hit rate (jackpot mode) | -0.0044 | -0.0121 | 0.0036 | no clear difference |
| ensemble | Exact-score log loss | 0.0004 | -0.0018 | 0.0026 | no clear difference |
| ensemble | Ranked probability score | -0.0000 | -0.0002 | 0.0001 | no clear difference |
| ensemble | Outcome Brier score | -0.0001 | -0.0010 | 0.0008 | no clear difference |
| ensemble | Points/fixture (points mode) | -0.0181 | -0.0464 | 0.0105 | no clear difference |
| ensemble | Points/fixture (jackpot mode) | -0.0875 | -0.1387 | -0.0411 | reference worse |
| ensemble | Exact-score hit rate (jackpot mode) | -0.0027 | -0.0114 | 0.0056 | no clear difference |

## By season (fold-by-fold)

| season | model | fixtures | exact_log_loss | rps | brier | outcome_log_loss | jackpot_exact | points_exact | jackpot_points | points_points | points_expected | flagged |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2018-19 | ensemble | 2264 | 2.8549 | 0.2062 | 0.6003 | 1.0031 | 0.1241 | 0.1064 | 1.1444 | 1.3167 | 1.3226 | 0.1661 |
| 2018-19 | ensemble_dc | 2264 | 2.8553 | 0.2061 | 0.6002 | 1.0028 | 0.1215 | 0.1051 | 1.0570 | 1.2986 | 1.2904 | 0.1661 |
| 2018-19 | dixon_coles | 2264 | 2.8737 | 0.2089 | 0.6058 | 1.0112 | 0.1259 | 0.1016 | 1.1170 | 1.2862 | 1.3093 | 0.0000 |

## Calibration — `ensemble_dc` home/draw/away probabilities

| bin | n | mean_forecast | observed | observed_se |
|---:|---:|---:|---:|---:|
| (-0.001, 0.05] | 21 | 0.0390 | 0.0476 | 0.0465 |
| (0.05, 0.1] | 96 | 0.0777 | 0.0312 | 0.0178 |
| (0.1, 0.15] | 193 | 0.1282 | 0.0725 | 0.0187 |
| (0.15, 0.2] | 367 | 0.1773 | 0.1689 | 0.0196 |
| (0.2, 0.3] | 2990 | 0.2646 | 0.2579 | 0.0080 |
| (0.3, 0.4] | 1428 | 0.3462 | 0.3466 | 0.0126 |
| (0.4, 0.5] | 921 | 0.4461 | 0.4441 | 0.0164 |
| (0.5, 0.6] | 471 | 0.5450 | 0.5924 | 0.0226 |
| (0.6, 0.7] | 182 | 0.6437 | 0.7033 | 0.0339 |
| (0.7, 0.8] | 82 | 0.7462 | 0.7561 | 0.0474 |
| (0.8, 1.0] | 41 | 0.8398 | 0.9756 | 0.0241 |

## Calibration — `ensemble_dc` probability of the jackpot pick

| bin | n | mean_forecast | observed | observed_se |
|---:|---:|---:|---:|---:|
| (0.08, 0.1] | 8 | 0.0960 | 0.0000 | 0.0000 |
| (0.1, 0.12] | 311 | 0.1140 | 0.1125 | 0.0179 |
| (0.12, 0.14] | 1630 | 0.1316 | 0.1252 | 0.0082 |
| (0.14, 0.16] | 308 | 0.1435 | 0.1136 | 0.0181 |
| (0.16, 0.2] | 7 | 0.1710 | 0.1429 | 0.1323 |

## Synthetic six-fixture rounds (NOT historical Super 6 rounds)

Six Saturday 15:00 fixtures per week drawn deterministically from E0, E1; the same fixtures for every model. Real Super 6 round lists are not available, so round-level numbers are illustrative of match-level skill only.

| model | rounds | points_mode_mean | jackpot_mode_mean | points_mode_exact_mean | jackpot_mode_exact_mean | all_six_exact | median_joint_probability |
|---:|---:|---:|---:|---:|---:|---:|---:|
| dixon_coles | 34 | 8.2353 | 6.7941 | 0.7647 | 0.6765 | 0 | 0.0000 |
| ensemble | 34 | 8.2059 | 6.8529 | 0.6765 | 0.6176 | 0 | 0.0000 |
| ensemble_dc | 34 | 7.3529 | 6.5588 | 0.4706 | 0.6176 | 0 | 0.0000 |

## Reading these numbers

- Exact scores are hard: even a good model's most likely score usually has a 10–15% chance. Six exact scores together are far less likely.
- A model counts as better only when the interval excludes zero, and the gain repeats across seasons.
