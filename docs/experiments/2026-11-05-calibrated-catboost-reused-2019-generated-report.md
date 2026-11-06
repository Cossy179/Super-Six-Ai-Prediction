# Walk-forward backtest — exp004-reused-2019

- Test window: **2019-07-01 → 2022-07-01** (UTC), competitions: E0, E1, E2, E3, SC0
- Fixtures per model: **6479** across 602 match days; refit every {'catboost_cal': 28.0, 'catboost_market_cal': 28.0} day(s)
- Rules: `sky-terms-2026-27` (verified: True); code `0.1.0+g242adf321aa3`
- Each cutoff is one minute before that day's first kick-off; models only see results published and odds collected before it.
- Hyperparameters are the documented defaults in `configs/model_defaults.yaml`; check that none were chosen using this window.

## Overall

| model | fixtures | exact_log_loss | rps | brier | outcome_log_loss | jackpot_exact | points_exact | jackpot_points | points_points | points_expected | flagged |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ensemble | 6479 | 2.8368 | 0.2102 | 0.6106 | 1.0193 | 0.1292 | 0.1198 | 1.1812 | 1.3406 | 1.3207 | 0.0696 |
| catboost_market | 6479 | 2.8518 | 0.2123 | 0.6154 | 1.0264 | 0.1283 | 0.1141 | 1.2013 | 1.2963 | 1.3454 | 0.0696 |
| catboost_market_cal | 6479 | 2.8525 | 0.2125 | 0.6158 | 1.0268 | 0.1269 | 0.1150 | 1.1505 | 1.3093 | 1.3096 | 0.0971 |
| dixon_coles | 6479 | 2.8656 | 0.2149 | 0.6205 | 1.0330 | 0.1241 | 0.1125 | 1.1054 | 1.2862 | 1.3087 | 0.0000 |
| catboost | 6479 | 2.8673 | 0.2160 | 0.6229 | 1.0370 | 0.1278 | 0.1144 | 1.1730 | 1.2825 | 1.3310 | 0.0000 |
| catboost_cal | 6479 | 2.8676 | 0.2159 | 0.6230 | 1.0369 | 0.1272 | 0.1142 | 1.1042 | 1.2811 | 1.2802 | 0.0275 |

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
| catboost_cal | Exact-score log loss | -0.0308 | -0.0373 | -0.0243 | reference better |
| catboost_cal | Ranked probability score | -0.0058 | -0.0073 | -0.0045 | reference better |
| catboost_cal | Outcome Brier score | -0.0124 | -0.0155 | -0.0096 | reference better |
| catboost_cal | Points/fixture (points mode) | 0.0596 | 0.0312 | 0.0902 | reference better |
| catboost_cal | Points/fixture (jackpot mode) | 0.0770 | 0.0394 | 0.1161 | reference better |
| catboost_cal | Exact-score hit rate (jackpot mode) | 0.0020 | -0.0051 | 0.0093 | no clear difference |
| catboost_market | Exact-score log loss | -0.0149 | -0.0200 | -0.0100 | reference better |
| catboost_market | Ranked probability score | -0.0022 | -0.0031 | -0.0012 | reference better |
| catboost_market | Outcome Brier score | -0.0048 | -0.0069 | -0.0026 | reference better |
| catboost_market | Points/fixture (points mode) | 0.0443 | 0.0208 | 0.0688 | reference better |
| catboost_market | Points/fixture (jackpot mode) | -0.0201 | -0.0535 | 0.0165 | no clear difference |
| catboost_market | Exact-score hit rate (jackpot mode) | 0.0009 | -0.0056 | 0.0078 | no clear difference |
| catboost_market_cal | Exact-score log loss | -0.0157 | -0.0205 | -0.0107 | reference better |
| catboost_market_cal | Ranked probability score | -0.0023 | -0.0034 | -0.0013 | reference better |
| catboost_market_cal | Outcome Brier score | -0.0052 | -0.0075 | -0.0031 | reference better |
| catboost_market_cal | Points/fixture (points mode) | 0.0313 | 0.0066 | 0.0565 | reference better |
| catboost_market_cal | Points/fixture (jackpot mode) | 0.0307 | -0.0043 | 0.0679 | no clear difference |
| catboost_market_cal | Exact-score hit rate (jackpot mode) | 0.0023 | -0.0037 | 0.0090 | no clear difference |
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
| 2019-20 | catboost_market_cal | 1808 | 2.8505 | 0.2097 | 0.6183 | 1.0311 | 0.1350 | 0.1139 | 1.1947 | 1.2954 | 1.2912 | 0.0077 |
| 2019-20 | catboost_market | 1808 | 2.8522 | 0.2095 | 0.6176 | 1.0304 | 0.1272 | 0.1162 | 1.2190 | 1.2920 | 1.3503 | 0.0077 |
| 2019-20 | catboost_cal | 1808 | 2.8634 | 0.2127 | 0.6244 | 1.0397 | 0.1350 | 0.1156 | 1.1449 | 1.2804 | 1.2643 | 0.0000 |
| 2019-20 | catboost | 1808 | 2.8635 | 0.2123 | 0.6233 | 1.0384 | 0.1350 | 0.1167 | 1.2246 | 1.2860 | 1.3292 | 0.0000 |
| 2019-20 | dixon_coles | 1808 | 2.8666 | 0.2114 | 0.6205 | 1.0334 | 0.1311 | 0.1139 | 1.1311 | 1.2876 | 1.2980 | 0.0000 |
| 2020-21 | ensemble | 2407 | 2.8436 | 0.2171 | 0.6187 | 1.0303 | 0.1275 | 0.1255 | 1.1305 | 1.3378 | 1.3140 | 0.1683 |
| 2020-21 | catboost_market | 2407 | 2.8521 | 0.2188 | 0.6221 | 1.0356 | 0.1255 | 0.1172 | 1.1309 | 1.2862 | 1.3349 | 0.1683 |
| 2020-21 | catboost_market_cal | 2407 | 2.8549 | 0.2187 | 0.6221 | 1.0351 | 0.1205 | 0.1155 | 1.0785 | 1.2896 | 1.3033 | 0.2422 |
| 2020-21 | dixon_coles | 2407 | 2.8679 | 0.2219 | 0.6291 | 1.0451 | 0.1205 | 0.1155 | 1.0669 | 1.2680 | 1.3157 | 0.0000 |
| 2020-21 | catboost | 2407 | 2.8689 | 0.2227 | 0.6303 | 1.0476 | 0.1176 | 0.1155 | 1.0798 | 1.2680 | 1.3294 | 0.0000 |
| 2020-21 | catboost_cal | 2407 | 2.8697 | 0.2221 | 0.6293 | 1.0452 | 0.1226 | 0.1151 | 1.0490 | 1.2651 | 1.2784 | 0.0740 |
| 2021-22 | ensemble | 2264 | 2.8290 | 0.2051 | 0.6006 | 1.0048 | 0.1228 | 0.1104 | 1.2005 | 1.3472 | 1.3299 | 0.0141 |
| 2021-22 | catboost_market | 2264 | 2.8510 | 0.2077 | 0.6065 | 1.0133 | 0.1321 | 0.1091 | 1.2619 | 1.3105 | 1.3528 | 0.0141 |
| 2021-22 | catboost_market_cal | 2264 | 2.8515 | 0.2080 | 0.6071 | 1.0145 | 0.1272 | 0.1153 | 1.1917 | 1.3414 | 1.3309 | 0.0141 |
| 2021-22 | dixon_coles | 2264 | 2.8624 | 0.2102 | 0.6112 | 1.0198 | 0.1223 | 0.1082 | 1.1259 | 1.3043 | 1.3100 | 0.0000 |
| 2021-22 | catboost | 2264 | 2.8686 | 0.2116 | 0.6147 | 1.0246 | 0.1330 | 0.1113 | 1.2310 | 1.2951 | 1.3341 | 0.0000 |
| 2021-22 | catboost_cal | 2264 | 2.8688 | 0.2120 | 0.6153 | 1.0258 | 0.1259 | 0.1122 | 1.1303 | 1.2986 | 1.2947 | 0.0000 |

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
| ensemble | 86 | 7.7209 | 6.9767 | 0.6279 | 0.7442 | 0 | 0.0000 |
| catboost | 86 | 7.6279 | 7.4302 | 0.6512 | 0.8488 | 0 | 0.0000 |
| dixon_coles | 86 | 7.6163 | 7.0116 | 0.6395 | 0.8023 | 0 | 0.0000 |
| catboost_market_cal | 86 | 7.4302 | 6.6163 | 0.5698 | 0.7558 | 0 | 0.0000 |
| catboost_market | 86 | 7.3721 | 6.9651 | 0.6047 | 0.7558 | 0 | 0.0000 |
| catboost_cal | 86 | 7.3256 | 6.7209 | 0.6047 | 0.8140 | 0 | 0.0000 |

## Reading these numbers

- Exact scores are hard: even a good model's most likely score usually has a 10–15% chance. Six exact scores together are far less likely.
- A model counts as better only when the interval excludes zero, and the gain repeats across seasons.
