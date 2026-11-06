# Walk-forward backtest — exp003-reused

- Test window: **2023-07-01 → 2026-11-05** (UTC), competitions: E0, E1, E2, E3, SC0
- Fixtures per model: **7441** across 692 match days; refit every {'ensemble': 1, 'dixon_coles': 1, 'catboost': 28.0, 'catboost_market': 28.0} day(s)
- Rules: `public-summary-2026-11-05` (verified: False); code `0.1.0+ge4f4838aaff5-dirty`
- Each cutoff is one minute before that day's first kick-off; models only see results published and odds collected before it.
- Hyperparameters are the documented defaults in `configs/model_defaults.yaml`; check that none were chosen using this window.

## Overall

| model | fixtures | exact_log_loss | rps | brier | outcome_log_loss | jackpot_exact | points_exact | jackpot_points | points_points | points_expected | flagged |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ensemble | 7441 | 2.8810 | 0.2095 | 0.6080 | 1.0150 | 0.1297 | 0.1125 | 1.1527 | 1.3260 | 1.3198 | 0.0274 |
| catboost_market | 7441 | 2.9071 | 0.2124 | 0.6142 | 1.0242 | 0.1270 | 0.1119 | 1.1758 | 1.3137 | 1.3480 | 0.0274 |
| dixon_coles | 7441 | 2.9071 | 0.2138 | 0.6170 | 1.0280 | 0.1269 | 0.1107 | 1.1203 | 1.2985 | 1.3011 | 0.0000 |
| catboost | 7441 | 2.9216 | 0.2155 | 0.6206 | 1.0335 | 0.1248 | 0.1103 | 1.1400 | 1.2954 | 1.3290 | 0.0000 |

## Paired comparison: `ensemble` minus each model

95% intervals from a block bootstrap over match days. For log loss, RPS and Brier a negative difference favours the reference; for points and hit rate a positive one does.

| versus | label | mean_diff | ci_low | ci_high | verdict |
|---:|---:|---:|---:|---:|---:|
| catboost | Exact-score log loss | -0.0405 | -0.0502 | -0.0317 | reference better |
| catboost | Ranked probability score | -0.0060 | -0.0074 | -0.0046 | reference better |
| catboost | Outcome Brier score | -0.0126 | -0.0157 | -0.0096 | reference better |
| catboost | Points/fixture (points mode) | 0.0306 | 0.0036 | 0.0605 | reference better |
| catboost | Points/fixture (jackpot mode) | 0.0126 | -0.0218 | 0.0433 | no clear difference |
| catboost | Exact-score hit rate (jackpot mode) | 0.0048 | -0.0014 | 0.0110 | no clear difference |
| catboost_market | Exact-score log loss | -0.0261 | -0.0337 | -0.0188 | reference better |
| catboost_market | Ranked probability score | -0.0029 | -0.0040 | -0.0017 | reference better |
| catboost_market | Outcome Brier score | -0.0061 | -0.0086 | -0.0036 | reference better |
| catboost_market | Points/fixture (points mode) | 0.0124 | -0.0087 | 0.0338 | no clear difference |
| catboost_market | Points/fixture (jackpot mode) | -0.0231 | -0.0507 | 0.0035 | no clear difference |
| catboost_market | Exact-score hit rate (jackpot mode) | 0.0027 | -0.0028 | 0.0081 | no clear difference |
| dixon_coles | Exact-score log loss | -0.0261 | -0.0318 | -0.0209 | reference better |
| dixon_coles | Ranked probability score | -0.0043 | -0.0055 | -0.0033 | reference better |
| dixon_coles | Outcome Brier score | -0.0090 | -0.0113 | -0.0067 | reference better |
| dixon_coles | Points/fixture (points mode) | 0.0276 | 0.0022 | 0.0533 | reference better |
| dixon_coles | Points/fixture (jackpot mode) | 0.0324 | 0.0026 | 0.0640 | reference better |
| dixon_coles | Exact-score hit rate (jackpot mode) | 0.0028 | -0.0032 | 0.0087 | no clear difference |

## By season (fold-by-fold)

| season | model | fixtures | exact_log_loss | rps | brier | outcome_log_loss | jackpot_exact | points_exact | jackpot_points | points_points | points_expected | flagged |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2023-24 | ensemble | 2264 | 2.9377 | 0.2090 | 0.5973 | 1.0005 | 0.1277 | 0.1117 | 1.1648 | 1.3405 | 1.3333 | 0.0468 |
| 2023-24 | catboost_market | 2264 | 2.9600 | 0.2123 | 0.6046 | 1.0113 | 0.1303 | 0.1153 | 1.2063 | 1.3397 | 1.3619 | 0.0468 |
| 2023-24 | dixon_coles | 2264 | 2.9651 | 0.2120 | 0.6042 | 1.0107 | 0.1223 | 0.1051 | 1.1603 | 1.3083 | 1.3283 | 0.0000 |
| 2023-24 | catboost | 2264 | 2.9692 | 0.2147 | 0.6100 | 1.0196 | 0.1184 | 0.1109 | 1.1166 | 1.3273 | 1.3377 | 0.0000 |
| 2024-25 | ensemble | 2264 | 2.8461 | 0.2086 | 0.6083 | 1.0146 | 0.1307 | 0.1131 | 1.1714 | 1.3313 | 1.3331 | 0.0146 |
| 2024-25 | catboost_market | 2264 | 2.8611 | 0.2091 | 0.6095 | 1.0167 | 0.1290 | 0.1170 | 1.2111 | 1.3361 | 1.3647 | 0.0146 |
| 2024-25 | dixon_coles | 2264 | 2.8664 | 0.2123 | 0.6161 | 1.0255 | 0.1250 | 0.1175 | 1.1162 | 1.3322 | 1.3087 | 0.0000 |
| 2024-25 | catboost | 2264 | 2.8765 | 0.2123 | 0.6167 | 1.0266 | 0.1334 | 0.1153 | 1.2129 | 1.3246 | 1.3357 | 0.0000 |
| 2025-26 | ensemble | 2264 | 2.8436 | 0.2105 | 0.6130 | 1.0216 | 0.1303 | 0.1206 | 1.1268 | 1.3520 | 1.3063 | 0.0287 |
| 2025-26 | catboost_market | 2264 | 2.8561 | 0.2124 | 0.6174 | 1.0283 | 0.1259 | 0.1122 | 1.1321 | 1.3286 | 1.3207 | 0.0287 |
| 2025-26 | catboost | 2264 | 2.8714 | 0.2160 | 0.6245 | 1.0389 | 0.1272 | 0.1109 | 1.1104 | 1.2875 | 1.3036 | 0.0000 |
| 2025-26 | dixon_coles | 2264 | 2.8735 | 0.2165 | 0.6251 | 1.0395 | 0.1316 | 0.1184 | 1.0892 | 1.3030 | 1.2771 | 0.0000 |
| 2026-27 | ensemble | 649 | 2.9360 | 0.2112 | 0.6275 | 1.0436 | 0.1310 | 0.0847 | 1.1356 | 1.1664 | 1.2731 | 0.0000 |
| 2026-27 | dixon_coles | 649 | 2.9644 | 0.2163 | 0.6369 | 1.0563 | 0.1325 | 0.0801 | 1.1032 | 1.1310 | 1.2633 | 0.0000 |
| 2026-27 | catboost_market | 649 | 3.0611 | 0.2241 | 0.6524 | 1.0813 | 0.1125 | 0.0817 | 1.0986 | 1.0924 | 1.3366 | 0.0000 |
| 2026-27 | catboost | 649 | 3.0874 | 0.2274 | 0.6578 | 1.0873 | 0.1094 | 0.0894 | 1.0709 | 1.1094 | 1.3636 | 0.0000 |

## Calibration — `ensemble` home/draw/away probabilities

| bin | n | mean_forecast | observed | observed_se |
|---:|---:|---:|---:|---:|
| (-0.001, 0.05] | 33 | 0.0403 | 0.0000 | 0.0000 |
| (0.05, 0.1] | 233 | 0.0796 | 0.0601 | 0.0156 |
| (0.1, 0.15] | 569 | 0.1280 | 0.0931 | 0.0122 |
| (0.15, 0.2] | 1387 | 0.1785 | 0.1839 | 0.0104 |
| (0.2, 0.3] | 9888 | 0.2520 | 0.2547 | 0.0044 |
| (0.3, 0.4] | 4046 | 0.3495 | 0.3487 | 0.0075 |
| (0.4, 0.5] | 3187 | 0.4453 | 0.4358 | 0.0088 |
| (0.5, 0.6] | 1885 | 0.5441 | 0.5501 | 0.0115 |
| (0.6, 0.7] | 713 | 0.6431 | 0.6396 | 0.0180 |
| (0.7, 0.8] | 289 | 0.7423 | 0.7751 | 0.0246 |
| (0.8, 1.0] | 93 | 0.8353 | 0.9032 | 0.0307 |

## Calibration — `ensemble` probability of the jackpot pick

| bin | n | mean_forecast | observed | observed_se |
|---:|---:|---:|---:|---:|
| (0.06, 0.08] | 1 | 0.0760 | 0.0000 | 0.0000 |
| (0.08, 0.1] | 348 | 0.0967 | 0.1178 | 0.0173 |
| (0.1, 0.12] | 2862 | 0.1128 | 0.1136 | 0.0059 |
| (0.12, 0.14] | 3999 | 0.1268 | 0.1388 | 0.0055 |
| (0.14, 0.16] | 219 | 0.1463 | 0.1781 | 0.0259 |
| (0.16, 0.2] | 12 | 0.1647 | 0.4167 | 0.1423 |

## Synthetic six-fixture rounds (NOT historical Super 6 rounds)

Six Saturday 15:00 fixtures per week drawn deterministically from E0, E1; the same fixtures for every model. Real Super 6 round lists are not available, so round-level numbers are illustrative of match-level skill only.

| model | rounds | points_mode_mean | jackpot_mode_mean | points_mode_exact_mean | jackpot_mode_exact_mean | all_six_exact | median_joint_probability |
|---:|---:|---:|---:|---:|---:|---:|---:|
| ensemble | 98 | 8.1735 | 7.0408 | 0.7245 | 0.8163 | 0 | 0.0000 |
| dixon_coles | 98 | 8.1531 | 6.8265 | 0.7041 | 0.8061 | 0 | 0.0000 |
| catboost_market | 98 | 7.9796 | 7.0306 | 0.6531 | 0.7857 | 0 | 0.0000 |
| catboost | 98 | 7.9388 | 7.1531 | 0.6735 | 0.8265 | 0 | 0.0000 |

## Reading these numbers

- Exact scores are hard: even a good model's most likely score usually has a 10–15% chance. Six exact scores together are far less likely.
- A model counts as better only when the interval excludes zero, and the gain repeats across seasons.
