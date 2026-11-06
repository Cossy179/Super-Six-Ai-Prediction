# Walk-forward backtest — exp003-fresh

- Test window: **2019-07-01 → 2022-07-01** (UTC), competitions: E0, E1, E2, E3, SC0
- Fixtures per model: **6479** across 602 match days; refit every {'ensemble': 1, 'dixon_coles': 1, 'catboost': 28.0, 'catboost_no_shots': 28.0, 'catboost_elo_goals': 28.0, 'catboost_market': 28.0} day(s)
- Rules: `public-summary-2026-11-05` (verified: False); code `0.1.0+ge4f4838aaff5`
- Each cutoff is one minute before that day's first kick-off; models only see results published and odds collected before it.
- Hyperparameters are the documented defaults in `configs/model_defaults.yaml`; check that none were chosen using this window.

## Overall

| model | fixtures | exact_log_loss | rps | brier | outcome_log_loss | jackpot_exact | points_exact | jackpot_points | points_points | points_expected | flagged |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ensemble | 6479 | 2.8368 | 0.2102 | 0.6106 | 1.0193 | 0.1292 | 0.1198 | 1.1812 | 1.3406 | 1.3207 | 0.0696 |
| catboost_market | 6479 | 2.8518 | 0.2123 | 0.6154 | 1.0264 | 0.1283 | 0.1141 | 1.2013 | 1.2963 | 1.3454 | 0.0696 |
| dixon_coles | 6479 | 2.8656 | 0.2149 | 0.6205 | 1.0330 | 0.1241 | 0.1125 | 1.1054 | 1.2862 | 1.3087 | 0.0000 |
| catboost | 6479 | 2.8673 | 0.2160 | 0.6229 | 1.0370 | 0.1278 | 0.1144 | 1.1730 | 1.2825 | 1.3310 | 0.0000 |
| catboost_no_shots | 6479 | 2.8735 | 0.2170 | 0.6250 | 1.0402 | 0.1249 | 0.1110 | 1.1176 | 1.2695 | 1.3130 | 0.0000 |
| catboost_elo_goals | 6479 | 2.8748 | 0.2171 | 0.6253 | 1.0405 | 0.1230 | 0.1104 | 1.1139 | 1.2655 | 1.3125 | 0.0000 |

## Paired comparison: `ensemble` minus each model

95% intervals from a block bootstrap over match days. For log loss, RPS and Brier a negative difference favours the reference; for points and hit rate a positive one does.

| versus | label | mean_diff | ci_low | ci_high | verdict |
|---:|---:|---:|---:|---:|---:|
| catboost | Exact-score log loss | -0.0305 | -0.0371 | -0.0237 | reference better |
| catboost | Ranked probability score | -0.0058 | -0.0072 | -0.0045 | reference better |
| catboost | Outcome Brier score | -0.0123 | -0.0154 | -0.0094 | reference better |
| catboost | Points/fixture (points mode) | 0.0582 | 0.0309 | 0.0861 | reference better |
| catboost | Points/fixture (jackpot mode) | 0.0082 | -0.0266 | 0.0446 | no clear difference |
| catboost | Exact-score hit rate (jackpot mode) | 0.0014 | -0.0055 | 0.0085 | no clear difference |
| catboost_elo_goals | Exact-score log loss | -0.0380 | -0.0448 | -0.0310 | reference better |
| catboost_elo_goals | Ranked probability score | -0.0069 | -0.0083 | -0.0056 | reference better |
| catboost_elo_goals | Outcome Brier score | -0.0146 | -0.0176 | -0.0119 | reference better |
| catboost_elo_goals | Points/fixture (points mode) | 0.0752 | 0.0443 | 0.1067 | reference better |
| catboost_elo_goals | Points/fixture (jackpot mode) | 0.0673 | 0.0304 | 0.1039 | reference better |
| catboost_elo_goals | Exact-score hit rate (jackpot mode) | 0.0062 | -0.0008 | 0.0130 | no clear difference |
| catboost_market | Exact-score log loss | -0.0149 | -0.0200 | -0.0100 | reference better |
| catboost_market | Ranked probability score | -0.0022 | -0.0031 | -0.0012 | reference better |
| catboost_market | Outcome Brier score | -0.0048 | -0.0069 | -0.0026 | reference better |
| catboost_market | Points/fixture (points mode) | 0.0443 | 0.0208 | 0.0688 | reference better |
| catboost_market | Points/fixture (jackpot mode) | -0.0201 | -0.0535 | 0.0165 | no clear difference |
| catboost_market | Exact-score hit rate (jackpot mode) | 0.0009 | -0.0056 | 0.0078 | no clear difference |
| catboost_no_shots | Exact-score log loss | -0.0367 | -0.0435 | -0.0297 | reference better |
| catboost_no_shots | Ranked probability score | -0.0069 | -0.0083 | -0.0054 | reference better |
| catboost_no_shots | Outcome Brier score | -0.0143 | -0.0174 | -0.0114 | reference better |
| catboost_no_shots | Points/fixture (points mode) | 0.0712 | 0.0399 | 0.1032 | reference better |
| catboost_no_shots | Points/fixture (jackpot mode) | 0.0636 | 0.0258 | 0.1018 | reference better |
| catboost_no_shots | Exact-score hit rate (jackpot mode) | 0.0043 | -0.0028 | 0.0115 | no clear difference |
| dixon_coles | Exact-score log loss | -0.0288 | -0.0347 | -0.0230 | reference better |
| dixon_coles | Ranked probability score | -0.0047 | -0.0060 | -0.0035 | reference better |
| dixon_coles | Outcome Brier score | -0.0098 | -0.0125 | -0.0071 | reference better |
| dixon_coles | Points/fixture (points mode) | 0.0545 | 0.0265 | 0.0833 | reference better |
| dixon_coles | Points/fixture (jackpot mode) | 0.0758 | 0.0417 | 0.1114 | reference better |
| dixon_coles | Exact-score hit rate (jackpot mode) | 0.0051 | -0.0012 | 0.0115 | no clear difference |

## By season (fold-by-fold)

| season | model | fixtures | exact_log_loss | rps | brier | outcome_log_loss | jackpot_exact | points_exact | jackpot_points | points_points | points_expected | flagged |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2019-20 | ensemble | 1808 | 2.8377 | 0.2073 | 0.6125 | 1.0229 | 0.1394 | 0.1239 | 1.2246 | 1.3363 | 1.3180 | 0.0077 |
| 2019-20 | catboost_market | 1808 | 2.8522 | 0.2095 | 0.6176 | 1.0304 | 0.1272 | 0.1162 | 1.2190 | 1.2920 | 1.3503 | 0.0077 |
| 2019-20 | catboost | 1808 | 2.8635 | 0.2123 | 0.6233 | 1.0384 | 0.1350 | 0.1167 | 1.2246 | 1.2860 | 1.3292 | 0.0000 |
| 2019-20 | dixon_coles | 1808 | 2.8666 | 0.2114 | 0.6205 | 1.0334 | 0.1311 | 0.1139 | 1.1311 | 1.2876 | 1.2980 | 0.0000 |
| 2019-20 | catboost_elo_goals | 1808 | 2.8698 | 0.2137 | 0.6259 | 1.0413 | 0.1239 | 0.1095 | 1.1460 | 1.2555 | 1.3117 | 0.0000 |
| 2019-20 | catboost_no_shots | 1808 | 2.8733 | 0.2142 | 0.6270 | 1.0428 | 0.1228 | 0.1112 | 1.1361 | 1.2594 | 1.3088 | 0.0000 |
| 2020-21 | ensemble | 2407 | 2.8436 | 0.2171 | 0.6187 | 1.0303 | 0.1275 | 0.1255 | 1.1305 | 1.3378 | 1.3140 | 0.1683 |
| 2020-21 | catboost_market | 2407 | 2.8521 | 0.2188 | 0.6221 | 1.0356 | 0.1255 | 0.1172 | 1.1309 | 1.2862 | 1.3349 | 0.1683 |
| 2020-21 | dixon_coles | 2407 | 2.8679 | 0.2219 | 0.6291 | 1.0451 | 0.1205 | 0.1155 | 1.0669 | 1.2680 | 1.3157 | 0.0000 |
| 2020-21 | catboost | 2407 | 2.8689 | 0.2227 | 0.6303 | 1.0476 | 0.1176 | 0.1155 | 1.0798 | 1.2680 | 1.3294 | 0.0000 |
| 2020-21 | catboost_no_shots | 2407 | 2.8775 | 0.2237 | 0.6323 | 1.0511 | 0.1188 | 0.1138 | 1.0528 | 1.2655 | 1.3163 | 0.0000 |
| 2020-21 | catboost_elo_goals | 2407 | 2.8844 | 0.2244 | 0.6343 | 1.0540 | 0.1151 | 0.1147 | 1.0341 | 1.2605 | 1.3148 | 0.0000 |
| 2021-22 | ensemble | 2264 | 2.8290 | 0.2051 | 0.6006 | 1.0048 | 0.1228 | 0.1104 | 1.2005 | 1.3472 | 1.3299 | 0.0141 |
| 2021-22 | catboost_market | 2264 | 2.8510 | 0.2077 | 0.6065 | 1.0133 | 0.1321 | 0.1091 | 1.2619 | 1.3105 | 1.3528 | 0.0141 |
| 2021-22 | dixon_coles | 2264 | 2.8624 | 0.2102 | 0.6112 | 1.0198 | 0.1223 | 0.1082 | 1.1259 | 1.3043 | 1.3100 | 0.0000 |
| 2021-22 | catboost | 2264 | 2.8686 | 0.2116 | 0.6147 | 1.0246 | 0.1330 | 0.1113 | 1.2310 | 1.2951 | 1.3341 | 0.0000 |
| 2021-22 | catboost_elo_goals | 2264 | 2.8687 | 0.2120 | 0.6152 | 1.0256 | 0.1307 | 0.1064 | 1.1731 | 1.2787 | 1.3106 | 0.0000 |
| 2021-22 | catboost_no_shots | 2264 | 2.8694 | 0.2122 | 0.6156 | 1.0265 | 0.1330 | 0.1078 | 1.1718 | 1.2818 | 1.3128 | 0.0000 |

## Calibration — `ensemble` home/draw/away probabilities

| bin | n | mean_forecast | observed | observed_se |
|---:|---:|---:|---:|---:|
| (-0.001, 0.05] | 45 | 0.0415 | 0.0444 | 0.0307 |
| (0.05, 0.1] | 230 | 0.0778 | 0.0565 | 0.0152 |
| (0.1, 0.15] | 477 | 0.1267 | 0.1069 | 0.0141 |
| (0.15, 0.2] | 1018 | 0.1772 | 0.1768 | 0.0120 |
| (0.2, 0.3] | 8642 | 0.2579 | 0.2611 | 0.0047 |
| (0.3, 0.4] | 3946 | 0.3498 | 0.3401 | 0.0075 |
| (0.4, 0.5] | 2881 | 0.4451 | 0.4419 | 0.0093 |
| (0.5, 0.6] | 1318 | 0.5441 | 0.5531 | 0.0137 |
| (0.6, 0.7] | 529 | 0.6415 | 0.6749 | 0.0204 |
| (0.7, 0.8] | 232 | 0.7404 | 0.7284 | 0.0292 |
| (0.8, 1.0] | 119 | 0.8328 | 0.8992 | 0.0276 |

## Calibration — `ensemble` probability of the jackpot pick

| bin | n | mean_forecast | observed | observed_se |
|---:|---:|---:|---:|---:|
| (0.08, 0.1] | 61 | 0.0978 | 0.1475 | 0.0454 |
| (0.1, 0.12] | 1309 | 0.1138 | 0.1123 | 0.0087 |
| (0.12, 0.14] | 4724 | 0.1281 | 0.1331 | 0.0049 |
| (0.14, 0.16] | 371 | 0.1461 | 0.1348 | 0.0177 |
| (0.16, 0.2] | 14 | 0.1657 | 0.1429 | 0.0935 |

## Synthetic six-fixture rounds (NOT historical Super 6 rounds)

Six Saturday 15:00 fixtures per week drawn deterministically from E0, E1; the same fixtures for every model. Real Super 6 round lists are not available, so round-level numbers are illustrative of match-level skill only.

| model | rounds | points_mode_mean | jackpot_mode_mean | points_mode_exact_mean | jackpot_mode_exact_mean | all_six_exact | median_joint_probability |
|---:|---:|---:|---:|---:|---:|---:|---:|
| catboost_no_shots | 86 | 7.8837 | 6.6395 | 0.6744 | 0.7791 | 0 | 0.0000 |
| ensemble | 86 | 7.7209 | 6.9767 | 0.6279 | 0.7442 | 0 | 0.0000 |
| catboost | 86 | 7.6279 | 7.4302 | 0.6512 | 0.8488 | 0 | 0.0000 |
| dixon_coles | 86 | 7.6163 | 7.0116 | 0.6395 | 0.8023 | 0 | 0.0000 |
| catboost_elo_goals | 86 | 7.4884 | 6.6279 | 0.6047 | 0.7907 | 0 | 0.0000 |
| catboost_market | 86 | 7.3721 | 6.9651 | 0.6047 | 0.7558 | 0 | 0.0000 |

## Reading these numbers

- Exact scores are hard: even a good model's most likely score usually has a 10–15% chance. Six exact scores together are far less likely.
- A model counts as better only when the interval excludes zero, and the gain repeats across seasons.
