# Walk-forward backtest — test-2023-07_2026-11

- Test window: **2023-07-01 → 2026-11-05** (UTC), competitions: E0, E1, E2, E3, SC0
- Fixtures per model: **7441** across 692 match days; refit every 1 day(s)
- Rules: `public-summary-2026-11-05` (verified: False); code `0.1.0+g4c3a611d6006`
- Each cutoff is one minute before that day's first kick-off; models only see results published and odds collected before it.
- Hyperparameters are the documented defaults in `configs/model_defaults.yaml`; none were tuned on this window (Dixon–Coles/Poisson `xi`/`ridge` were checked on the 2022-23 validation season only).

## Overall

| model | fixtures | exact_log_loss | rps | brier | outcome_log_loss | jackpot_exact | points_exact | jackpot_points | points_points | points_expected | flagged |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| market_poisson | 7441 | 2.8818 | 0.2098 | 0.6086 | 1.0158 | 0.1286 | 0.1122 | 1.1427 | 1.3233 | 1.3170 | 0.0274 |
| poisson | 7441 | 2.9071 | 0.2138 | 0.6170 | 1.0280 | 0.1257 | 0.1102 | 1.1500 | 1.2958 | 1.3117 | 0.0000 |
| dixon_coles | 7441 | 2.9071 | 0.2138 | 0.6170 | 1.0280 | 0.1269 | 0.1107 | 1.1203 | 1.2985 | 1.3011 | 0.0000 |
| league_average | 7441 | 2.9689 | 0.2299 | 0.6498 | 1.0739 | 0.1207 | 0.1017 | 0.8722 | 1.1777 | 1.1829 | 0.0000 |
| rolling_goals | 7441 | 2.9957 | 0.2254 | 0.6424 | 1.0666 | 0.1126 | 0.1020 | 1.1195 | 1.2131 | 1.4022 | 0.0000 |
| empirical_score | 7441 | 3.0205 | 0.2300 | 0.6504 | 1.0748 | 0.1176 | 0.0989 | 0.9040 | 1.1692 | 1.1801 | 0.0000 |

## Paired comparison: `dixon_coles` minus each model

95% intervals from a block bootstrap over match days. For log loss, RPS and Brier a negative difference favours the reference; for points and hit rate a positive one does.

| versus | label | mean_diff | ci_low | ci_high | verdict |
|---:|---:|---:|---:|---:|---:|
| empirical_score | Exact-score log loss | -0.1134 | -0.1229 | -0.1041 | reference better |
| empirical_score | Ranked probability score | -0.0162 | -0.0186 | -0.0139 | reference better |
| empirical_score | Outcome Brier score | -0.0334 | -0.0384 | -0.0286 | reference better |
| empirical_score | Points/fixture (points mode) | 0.1293 | 0.0929 | 0.1636 | reference better |
| empirical_score | Points/fixture (jackpot mode) | 0.2162 | 0.1792 | 0.2533 | reference better |
| empirical_score | Exact-score hit rate (jackpot mode) | 0.0093 | 0.0029 | 0.0155 | reference better |
| league_average | Exact-score log loss | -0.0618 | -0.0710 | -0.0531 | reference better |
| league_average | Ranked probability score | -0.0161 | -0.0185 | -0.0138 | reference better |
| league_average | Outcome Brier score | -0.0328 | -0.0379 | -0.0281 | reference better |
| league_average | Points/fixture (points mode) | 0.1208 | 0.0844 | 0.1566 | reference better |
| league_average | Points/fixture (jackpot mode) | 0.2481 | 0.2142 | 0.2827 | reference better |
| league_average | Exact-score hit rate (jackpot mode) | 0.0062 | 0.0003 | 0.0121 | reference better |
| market_poisson | Exact-score log loss | 0.0254 | 0.0200 | 0.0312 | reference worse |
| market_poisson | Ranked probability score | 0.0040 | 0.0029 | 0.0052 | reference worse |
| market_poisson | Outcome Brier score | 0.0084 | 0.0060 | 0.0108 | reference worse |
| market_poisson | Points/fixture (points mode) | -0.0249 | -0.0523 | 0.0014 | no clear difference |
| market_poisson | Points/fixture (jackpot mode) | -0.0224 | -0.0547 | 0.0102 | no clear difference |
| market_poisson | Exact-score hit rate (jackpot mode) | -0.0017 | -0.0077 | 0.0045 | no clear difference |
| poisson | Exact-score log loss | 0.0001 | -0.0004 | 0.0005 | no clear difference |
| poisson | Ranked probability score | -0.0000 | -0.0000 | 0.0000 | no clear difference |
| poisson | Outcome Brier score | -0.0000 | -0.0002 | 0.0001 | no clear difference |
| poisson | Points/fixture (points mode) | 0.0027 | -0.0030 | 0.0086 | no clear difference |
| poisson | Points/fixture (jackpot mode) | -0.0297 | -0.0504 | -0.0085 | reference worse |
| poisson | Exact-score hit rate (jackpot mode) | 0.0012 | -0.0023 | 0.0049 | no clear difference |
| rolling_goals | Exact-score log loss | -0.0886 | -0.0999 | -0.0779 | reference better |
| rolling_goals | Ranked probability score | -0.0115 | -0.0137 | -0.0094 | reference better |
| rolling_goals | Outcome Brier score | -0.0254 | -0.0300 | -0.0207 | reference better |
| rolling_goals | Points/fixture (points mode) | 0.0853 | 0.0526 | 0.1164 | reference better |
| rolling_goals | Points/fixture (jackpot mode) | 0.0008 | -0.0428 | 0.0432 | no clear difference |
| rolling_goals | Exact-score hit rate (jackpot mode) | 0.0142 | 0.0064 | 0.0225 | reference better |

## By season (fold-by-fold)

| season | model | fixtures | exact_log_loss | rps | brier | outcome_log_loss | jackpot_exact | points_exact | jackpot_points | points_points | points_expected | flagged |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2023-24 | market_poisson | 2264 | 2.9401 | 0.2095 | 0.5984 | 1.0021 | 0.1259 | 0.1095 | 1.1462 | 1.3295 | 1.3283 | 0.0468 |
| 2023-24 | poisson | 2264 | 2.9648 | 0.2120 | 0.6040 | 1.0104 | 0.1197 | 0.1051 | 1.1842 | 1.3065 | 1.3372 | 0.0000 |
| 2023-24 | dixon_coles | 2264 | 2.9651 | 0.2120 | 0.6042 | 1.0107 | 0.1223 | 0.1051 | 1.1603 | 1.3083 | 1.3283 | 0.0000 |
| 2023-24 | league_average | 2264 | 3.0465 | 0.2325 | 0.6466 | 1.0688 | 0.1117 | 0.0958 | 0.8105 | 1.1718 | 1.1923 | 0.0000 |
| 2023-24 | rolling_goals | 2264 | 3.0495 | 0.2271 | 0.6370 | 1.0603 | 0.1117 | 0.1091 | 1.1179 | 1.2602 | 1.4050 | 0.0000 |
| 2023-24 | empirical_score | 2264 | 3.0936 | 0.2324 | 0.6465 | 1.0687 | 0.1078 | 0.0919 | 0.8852 | 1.1599 | 1.1902 | 0.0000 |
| 2024-25 | market_poisson | 2264 | 2.8465 | 0.2087 | 0.6086 | 1.0151 | 0.1307 | 0.1122 | 1.1678 | 1.3251 | 1.3310 | 0.0146 |
| 2024-25 | poisson | 2264 | 2.8662 | 0.2123 | 0.6161 | 1.0257 | 0.1250 | 0.1157 | 1.1444 | 1.3251 | 1.3186 | 0.0000 |
| 2024-25 | dixon_coles | 2264 | 2.8664 | 0.2123 | 0.6161 | 1.0255 | 0.1250 | 0.1175 | 1.1162 | 1.3322 | 1.3087 | 0.0000 |
| 2024-25 | league_average | 2264 | 2.9390 | 0.2289 | 0.6499 | 1.0740 | 0.1206 | 0.1056 | 0.8803 | 1.1877 | 1.1780 | 0.0000 |
| 2024-25 | rolling_goals | 2264 | 2.9577 | 0.2221 | 0.6372 | 1.0578 | 0.1153 | 0.1078 | 1.1391 | 1.2323 | 1.4155 | 0.0000 |
| 2024-25 | empirical_score | 2264 | 2.9954 | 0.2292 | 0.6509 | 1.0756 | 0.1170 | 0.0994 | 0.8980 | 1.1692 | 1.1773 | 0.0000 |
| 2025-26 | market_poisson | 2264 | 2.8431 | 0.2107 | 0.6135 | 1.0223 | 0.1285 | 0.1228 | 1.1162 | 1.3604 | 1.3044 | 0.0287 |
| 2025-26 | poisson | 2264 | 2.8734 | 0.2165 | 0.6251 | 1.0396 | 0.1303 | 0.1193 | 1.1206 | 1.3057 | 1.2886 | 0.0000 |
| 2025-26 | dixon_coles | 2264 | 2.8735 | 0.2165 | 0.6251 | 1.0395 | 0.1316 | 0.1184 | 1.0892 | 1.3030 | 1.2771 | 0.0000 |
| 2025-26 | league_average | 2264 | 2.9114 | 0.2296 | 0.6515 | 1.0765 | 0.1263 | 0.1078 | 0.8975 | 1.1890 | 1.1796 | 0.0000 |
| 2025-26 | rolling_goals | 2264 | 2.9582 | 0.2265 | 0.6472 | 1.0741 | 0.1126 | 0.0981 | 1.1197 | 1.1899 | 1.3988 | 0.0000 |
| 2025-26 | empirical_score | 2264 | 2.9623 | 0.2298 | 0.6523 | 1.0779 | 0.1254 | 0.1082 | 0.9125 | 1.1904 | 1.1738 | 0.0000 |
| 2026-27 | market_poisson | 649 | 2.9360 | 0.2112 | 0.6275 | 1.0436 | 0.1310 | 0.0847 | 1.1356 | 1.1664 | 1.2731 | 0.0000 |
| 2026-27 | dixon_coles | 649 | 2.9644 | 0.2163 | 0.6369 | 1.0563 | 0.1325 | 0.0801 | 1.1032 | 1.1310 | 1.2633 | 0.0000 |
| 2026-27 | poisson | 649 | 2.9654 | 0.2164 | 0.6377 | 1.0576 | 0.1325 | 0.0770 | 1.1525 | 1.1217 | 1.2787 | 0.0000 |
| 2026-27 | league_average | 649 | 3.0037 | 0.2251 | 0.6553 | 1.0824 | 0.1325 | 0.0878 | 0.9707 | 1.1233 | 1.1783 | 0.0000 |
| 2026-27 | empirical_score | 649 | 3.0560 | 0.2250 | 0.6555 | 1.0830 | 0.1263 | 0.0894 | 0.9615 | 1.1279 | 1.1763 | 0.0000 |
| 2026-27 | rolling_goals | 649 | 3.0721 | 0.2269 | 0.6621 | 1.0934 | 0.1063 | 0.0709 | 1.0555 | 1.0632 | 1.3584 | 0.0000 |

## Calibration — `dixon_coles` home/draw/away probabilities

| bin | n | mean_forecast | observed | observed_se |
|---:|---:|---:|---:|---:|
| (-0.001, 0.05] | 50 | 0.0353 | 0.0000 | 0.0000 |
| (0.05, 0.1] | 208 | 0.0779 | 0.0913 | 0.0200 |
| (0.1, 0.15] | 478 | 0.1294 | 0.1130 | 0.0145 |
| (0.15, 0.2] | 1138 | 0.1788 | 0.1670 | 0.0111 |
| (0.2, 0.3] | 9869 | 0.2570 | 0.2609 | 0.0044 |
| (0.3, 0.4] | 4783 | 0.3470 | 0.3429 | 0.0069 |
| (0.4, 0.5] | 3266 | 0.4463 | 0.4455 | 0.0087 |
| (0.5, 0.6] | 1621 | 0.5426 | 0.5330 | 0.0124 |
| (0.6, 0.7] | 618 | 0.6420 | 0.6731 | 0.0189 |
| (0.7, 0.8] | 202 | 0.7475 | 0.7426 | 0.0308 |
| (0.8, 1.0] | 90 | 0.8516 | 0.8667 | 0.0358 |

## Calibration — `dixon_coles` probability of the jackpot pick

| bin | n | mean_forecast | observed | observed_se |
|---:|---:|---:|---:|---:|
| (0.06, 0.08] | 2 | 0.0773 | 0.0000 | 0.0000 |
| (0.08, 0.1] | 384 | 0.0958 | 0.1068 | 0.0158 |
| (0.1, 0.12] | 2178 | 0.1119 | 0.1148 | 0.0068 |
| (0.12, 0.14] | 4189 | 0.1295 | 0.1318 | 0.0052 |
| (0.14, 0.16] | 555 | 0.1479 | 0.1351 | 0.0145 |
| (0.16, 0.2] | 132 | 0.1681 | 0.1970 | 0.0346 |
| (0.2, 1.0] | 1 | 0.2018 | 0.0000 | 0.0000 |

## Synthetic six-fixture rounds (NOT historical Super 6 rounds)

Six Saturday 15:00 fixtures per week drawn deterministically from E0, E1; the same fixtures for every model. Real Super 6 round lists are not available, so round-level numbers are illustrative of match-level skill only.

| model | rounds | points_mode_mean | jackpot_mode_mean | points_mode_exact_mean | jackpot_mode_exact_mean | all_six_exact | median_joint_probability |
|---:|---:|---:|---:|---:|---:|---:|---:|
| market_poisson | 98 | 8.1735 | 7.0408 | 0.7245 | 0.8163 | 0 | 0.0000 |
| dixon_coles | 98 | 8.1531 | 6.8265 | 0.7041 | 0.8061 | 0 | 0.0000 |
| poisson | 98 | 8.1531 | 7.1122 | 0.7041 | 0.7857 | 0 | 0.0000 |
| rolling_goals | 98 | 7.5102 | 6.5816 | 0.6122 | 0.6429 | 0 | 0.0000 |
| league_average | 98 | 7.4592 | 4.9898 | 0.6020 | 0.7245 | 0 | 0.0000 |
| empirical_score | 98 | 7.4286 | 5.1531 | 0.5918 | 0.7041 | 0 | 0.0000 |

## Reading these numbers

- Exact scores are hard: even a good model's most likely score usually has a 10–15% chance. Six exact scores together are far less likely.
- A model counts as better only when the interval excludes zero, and the gain repeats across seasons.
