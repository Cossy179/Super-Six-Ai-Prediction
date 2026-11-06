# Experiments

Each experiment's protocol and promotion rule were committed **before** its results were
seen; the commit is linked in each write-up. Every run is also logged automatically to
`reports/experiments.jsonl`. List the log with `super6 experiments`.

| # | Question | Fresh test window | Decision |
|---|---|---|---|
| [001](2026-11-05-phase1-walk-forward.md) | Do team-strength models beat simple baselines? | 2023-07 → 2026-11 | Yes. Dixon–Coles beats all three baselines; the market beats Dixon–Coles; Dixon–Coles ≈ plain Poisson |
| [002](2026-11-05-ensemble-market-first.md) | Market-first ensemble vs Dixon–Coles | 2023–26, second use (weight fitted on 2022-23) | **Promoted** `ensemble` to the default |
| [003](2026-11-05-catboost-features.md) | CatBoost challengers and feature-family ablations | 2019-07 → 2022-07 | Not promoted. Shots kept in the CatBoost features; xG untestable |
| [004](2026-11-05-calibrated-catboost.md) | Recalibrated CatBoost challengers | 2018-07 → 2019-07 | Not promoted. Calibration fixed the overconfidence but not the accuracy |
| [005](2026-11-05-market-dc.md) | Dixon–Coles shape for the market model ("Model C") | 2018-07 → 2019-07 | Not promoted. No residual dependence; the draw shift overshoots |

## Window usage

| Season(s) | Used for |
|---|---|
| 2016-17 – 2017-18 | Training and feature burn-in only |
| 2018-19 | Fresh test for experiments 004 and 005 |
| 2019-20 – 2021-22 | Fresh test for experiment 003; consistency check afterwards |
| 2022-23 | Validation: Dixon–Coles `xi`/`ridge`, ensemble weight |
| 2023-24 – 2026-27 (to Nov) | Test for 001–002; consistency check afterwards |

## Phase 2 exit criteria (DESIGN §20)

| Criterion | Status |
|---|---|
| Reproducible walk-forward experiments | ✓ Every run logs its exact command, code version, window and configs. Protocols are committed before results |
| No feature contamination | ✓ Leakage tests cover the point-in-time view, the ML feature builder, `odds_as_of`, earlier views (`at`), and every model in the registry |
| Challenger promoted only when verified to outperform | ✓ One promotion (`ensemble`, experiment 002); five challengers rejected (two each in 003 and 004, one in 005) |
| Documented champion with ablation and calibration results | ✓ [`MODEL_CARD.md`](../MODEL_CARD.md); ablations in 003, calibration in 003–005 |
| Odds integration | ✓ Pre-match odds, with point-in-time availability |
| xG integration | Ingested (2026-27 only) and carried as an explicitly missing feature. **Not testable** until there is enough xG history (see below) |
| Model registry, experiment tracking | ✓ `model_registry` table; `reports/experiments.jsonl` + `super6 experiments` |
| Real Super 6 round replay | ✓ `super6 replay-rounds`; needs verified historical round files |

### Deferred: xG

The only xG in the current source is 2026-27 (from August 2026), for E0–E3 and SC0. A fair
test needs xG history to train on *and* an untouched season to test on. That means
re-running experiment 003's ablation with the `xg` family once two seasons exist (after
2027-28), or adding a licensed historical xG source.

No season is left untouched. The next truly fresh evidence is the **live shadow period**
(DESIGN §13.5): lock a forecast before every real round and compare after settlement.
