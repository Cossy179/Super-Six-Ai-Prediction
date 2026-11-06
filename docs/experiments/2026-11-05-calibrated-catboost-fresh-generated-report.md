# Walk-forward backtest — exp004-fresh

- Test window: **2018-07-01 → 2019-07-01** (UTC), competitions: E0, E1, E2, E3, SC0
- Fixtures per model: **2264** across 193 match days; refit every {'ensemble': 1, 'dixon_coles': 1, 'catboost': 28.0, 'catboost_cal': 28.0, 'catboost_market': 28.0, 'catboost_market_cal': 28.0} day(s)
- Rules: `sky-terms-2026-27` (verified: True); code `0.1.0+g6b35703ca8b3-dirty`
- Each cutoff is one minute before that day's first kick-off; models only see results published and odds collected before it.
- Hyperparameters are the documented defaults in `configs/model_defaults.yaml`; check that none were chosen using this window.

## Overall

| model | fixtures | exact_log_loss | rps | brier | outcome_log_loss | jackpot_exact | points_exact | jackpot_points | points_points | points_expected | flagged |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ensemble | 2264 | 2.8549 | 0.2062 | 0.6003 | 1.0031 | 0.1241 | 0.1064 | 1.1444 | 1.3167 | 1.3226 | 0.1661 |
| dixon_coles | 2264 | 2.8737 | 0.2089 | 0.6058 | 1.0112 | 0.1259 | 0.1016 | 1.1170 | 1.2862 | 1.3093 | 0.0000 |
| catboost_market | 2264 | 2.8752 | 0.2094 | 0.6075 | 1.0139 | 0.1237 | 0.1078 | 1.2032 | 1.2942 | 1.3617 | 0.1661 |
| catboost_market_cal | 2264 | 2.8758 | 0.2103 | 0.6092 | 1.0168 | 0.1299 | 0.1100 | 1.1555 | 1.3017 | 1.3009 | 0.1661 |
| catboost_cal | 2264 | 2.8940 | 0.2142 | 0.6173 | 1.0284 | 0.1263 | 0.1029 | 1.0972 | 1.2619 | 1.2742 | 0.0000 |
| catboost | 2264 | 2.8940 | 0.2136 | 0.6159 | 1.0261 | 0.1228 | 0.1007 | 1.1723 | 1.2473 | 1.3457 | 0.0000 |

## Paired comparison: `ensemble` minus each model

95% intervals from a block bootstrap over match days. For log loss, RPS and Brier a negative difference favours the reference; for points and hit rate a positive one does.

| versus | label | mean_diff | ci_low | ci_high | verdict |
|---:|---:|---:|---:|---:|---:|
| catboost | Exact-score log loss | -0.0391 | -0.0522 | -0.0263 | reference better |
| catboost | Ranked probability score | -0.0075 | -0.0101 | -0.0049 | reference better |
| catboost | Outcome Brier score | -0.0156 | -0.0213 | -0.0101 | reference better |
| catboost | Points/fixture (points mode) | 0.0693 | 0.0069 | 0.1288 | reference better |
| catboost | Points/fixture (jackpot mode) | -0.0278 | -0.0871 | 0.0357 | no clear difference |
| catboost | Exact-score hit rate (jackpot mode) | 0.0013 | -0.0097 | 0.0131 | no clear difference |
| catboost_cal | Exact-score log loss | -0.0391 | -0.0514 | -0.0279 | reference better |
| catboost_cal | Ranked probability score | -0.0081 | -0.0105 | -0.0057 | reference better |
| catboost_cal | Outcome Brier score | -0.0170 | -0.0223 | -0.0119 | reference better |
| catboost_cal | Points/fixture (points mode) | 0.0548 | 0.0058 | 0.1078 | reference better |
| catboost_cal | Points/fixture (jackpot mode) | 0.0473 | -0.0089 | 0.1079 | no clear difference |
| catboost_cal | Exact-score hit rate (jackpot mode) | -0.0022 | -0.0121 | 0.0087 | no clear difference |
| catboost_market | Exact-score log loss | -0.0203 | -0.0312 | -0.0104 | reference better |
| catboost_market | Ranked probability score | -0.0033 | -0.0052 | -0.0014 | reference better |
| catboost_market | Outcome Brier score | -0.0072 | -0.0115 | -0.0031 | reference better |
| catboost_market | Points/fixture (points mode) | 0.0225 | -0.0223 | 0.0680 | no clear difference |
| catboost_market | Points/fixture (jackpot mode) | -0.0587 | -0.1095 | -0.0043 | reference worse |
| catboost_market | Exact-score hit rate (jackpot mode) | 0.0004 | -0.0102 | 0.0113 | no clear difference |
| catboost_market_cal | Exact-score log loss | -0.0209 | -0.0313 | -0.0120 | reference better |
| catboost_market_cal | Ranked probability score | -0.0041 | -0.0059 | -0.0025 | reference better |
| catboost_market_cal | Outcome Brier score | -0.0089 | -0.0128 | -0.0052 | reference better |
| catboost_market_cal | Points/fixture (points mode) | 0.0150 | -0.0274 | 0.0576 | no clear difference |
| catboost_market_cal | Points/fixture (jackpot mode) | -0.0110 | -0.0579 | 0.0405 | no clear difference |
| catboost_market_cal | Exact-score hit rate (jackpot mode) | -0.0057 | -0.0153 | 0.0044 | no clear difference |
| dixon_coles | Exact-score log loss | -0.0188 | -0.0271 | -0.0112 | reference better |
| dixon_coles | Ranked probability score | -0.0027 | -0.0044 | -0.0011 | reference better |
| dixon_coles | Outcome Brier score | -0.0056 | -0.0093 | -0.0019 | reference better |
| dixon_coles | Points/fixture (points mode) | 0.0305 | -0.0109 | 0.0690 | no clear difference |
| dixon_coles | Points/fixture (jackpot mode) | 0.0274 | -0.0149 | 0.0727 | no clear difference |
| dixon_coles | Exact-score hit rate (jackpot mode) | -0.0018 | -0.0097 | 0.0069 | no clear difference |

## By season (fold-by-fold)

| season | model | fixtures | exact_log_loss | rps | brier | outcome_log_loss | jackpot_exact | points_exact | jackpot_points | points_points | points_expected | flagged |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2018-19 | ensemble | 2264 | 2.8549 | 0.2062 | 0.6003 | 1.0031 | 0.1241 | 0.1064 | 1.1444 | 1.3167 | 1.3226 | 0.1661 |
| 2018-19 | dixon_coles | 2264 | 2.8737 | 0.2089 | 0.6058 | 1.0112 | 0.1259 | 0.1016 | 1.1170 | 1.2862 | 1.3093 | 0.0000 |
| 2018-19 | catboost_market | 2264 | 2.8752 | 0.2094 | 0.6075 | 1.0139 | 0.1237 | 0.1078 | 1.2032 | 1.2942 | 1.3617 | 0.1661 |
| 2018-19 | catboost_market_cal | 2264 | 2.8758 | 0.2103 | 0.6092 | 1.0168 | 0.1299 | 0.1100 | 1.1555 | 1.3017 | 1.3009 | 0.1661 |
| 2018-19 | catboost_cal | 2264 | 2.8940 | 0.2142 | 0.6173 | 1.0284 | 0.1263 | 0.1029 | 1.0972 | 1.2619 | 1.2742 | 0.0000 |
| 2018-19 | catboost | 2264 | 2.8940 | 0.2136 | 0.6159 | 1.0261 | 0.1228 | 0.1007 | 1.1723 | 1.2473 | 1.3457 | 0.0000 |

## Calibration — `ensemble` home/draw/away probabilities

| bin | n | mean_forecast | observed | observed_se |
|---:|---:|---:|---:|---:|
| (-0.001, 0.05] | 18 | 0.0385 | 0.0000 | 0.0000 |
| (0.05, 0.1] | 88 | 0.0771 | 0.0341 | 0.0193 |
| (0.1, 0.15] | 181 | 0.1279 | 0.0773 | 0.0199 |
| (0.15, 0.2] | 370 | 0.1783 | 0.1676 | 0.0194 |
| (0.2, 0.3] | 2998 | 0.2568 | 0.2552 | 0.0080 |
| (0.3, 0.4] | 1353 | 0.3504 | 0.3481 | 0.0130 |
| (0.4, 0.5] | 966 | 0.4459 | 0.4286 | 0.0159 |
| (0.5, 0.6] | 502 | 0.5438 | 0.5956 | 0.0219 |
| (0.6, 0.7] | 192 | 0.6429 | 0.6927 | 0.0333 |
| (0.7, 0.8] | 83 | 0.7463 | 0.7590 | 0.0469 |
| (0.8, 1.0] | 41 | 0.8398 | 0.9756 | 0.0241 |

## Calibration — `ensemble` probability of the jackpot pick

| bin | n | mean_forecast | observed | observed_se |
|---:|---:|---:|---:|---:|
| (0.08, 0.1] | 15 | 0.0975 | 0.0667 | 0.0644 |
| (0.1, 0.12] | 540 | 0.1141 | 0.1370 | 0.0148 |
| (0.12, 0.14] | 1553 | 0.1278 | 0.1185 | 0.0082 |
| (0.14, 0.16] | 149 | 0.1469 | 0.1409 | 0.0285 |
| (0.16, 0.2] | 7 | 0.1718 | 0.1429 | 0.1323 |

## Synthetic six-fixture rounds (NOT historical Super 6 rounds)

Six Saturday 15:00 fixtures per week drawn deterministically from E0, E1; the same fixtures for every model. Real Super 6 round lists are not available, so round-level numbers are illustrative of match-level skill only.

| model | rounds | points_mode_mean | jackpot_mode_mean | points_mode_exact_mean | jackpot_mode_exact_mean | all_six_exact | median_joint_probability |
|---:|---:|---:|---:|---:|---:|---:|---:|
| dixon_coles | 34 | 8.2353 | 6.7941 | 0.7647 | 0.6765 | 0 | 0.0000 |
| ensemble | 34 | 8.2059 | 6.8529 | 0.6765 | 0.6176 | 0 | 0.0000 |
| catboost_cal | 34 | 7.9706 | 6.7059 | 0.6765 | 0.7059 | 0 | 0.0000 |
| catboost_market_cal | 34 | 7.9118 | 6.4118 | 0.6765 | 0.5882 | 0 | 0.0000 |
| catboost_market | 34 | 7.9118 | 6.2647 | 0.6176 | 0.5000 | 0 | 0.0000 |
| catboost | 34 | 7.4412 | 7.1471 | 0.5000 | 0.6176 | 0 | 0.0000 |

## Reading these numbers

- Exact scores are hard: even a good model's most likely score usually has a 10–15% chance. Six exact scores together are far less likely.
- A model counts as better only when the interval excludes zero, and the gain repeats across seasons.
