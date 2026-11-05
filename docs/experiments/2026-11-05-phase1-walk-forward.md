# Experiment 001: Phase 1 walk-forward backtest

- **Date:** 5 November 2026
- **Code:** `0.1.0+g4c3a611d6006` (model code unchanged since)
- **Data:** Football-Data.co.uk, E0–E3, EC, SC0, SC1, seasons 2016-17 to 2026-27 (30,239 results)
- **Rules:** `public-summary-2026-11-05` (5/2/0 points; **not yet verified** against official terms)
- **Full generated output:** [`2026-11-05-walk-forward-generated-report.md`](2026-11-05-walk-forward-generated-report.md)

## Protocol (declared before looking at test results)

1. **Validation window, 2022-07-01 → 2023-07-01.** Grid over Dixon–Coles `xi_per_day ∈
   {0.001, 0.0019, 0.003}` × `ridge ∈ {0.5, 2, 8}`, weekly refits, ranked by exact-score
   log loss ([raw output](2026-11-05-validation-2022-23.txt)). Chosen: `xi = 0.0019`,
   `ridge = 0.5`. The spread between the best and worst setting was small (2.865–2.875).
2. **Test window, 2023-07-01 → 2026-11-05, not used for any choice.** Every Premier League,
   EFL and Scottish Premiership fixture (7,441). The cutoff is one minute before each match
   day's first kick-off. All six models are refitted from scratch every match day and see
   only results published, and odds collected, before the cutoff.
3. Comparisons use a paired block bootstrap over match days (2,000 resamples).

Command: `super6 backtest --start 2023-07-01 --end 2026-11-05`.

## Results

| Model | Exact-score log loss ↓ | RPS ↓ | Exact hit rate (jackpot picks) | Points/fixture (points mode) |
|---|---:|---:|---:|---:|
| Market-implied Poisson | **2.882** | **0.2098** | 12.9% | 1.323 |
| Team-strength Poisson | 2.907 | 0.2138 | 12.6% | 1.296 |
| Dixon–Coles | 2.907 | 0.2138 | 12.7% | 1.299 |
| League average (no team info) | 2.969 | 0.2299 | 12.1% | 1.178 |
| Rolling goals (last 10) | 2.996 | 0.2254 | 11.3% | 1.213 |
| Empirical scorelines | 3.021 | 0.2300 | 11.8% | 1.169 |

Synthetic six-fixture rounds (Saturday 15:00 E0/E1 games, **not** real Super 6 rounds, 98
rounds): Dixon–Coles averaged 8.15 points per round in points mode against 7.43–7.51 for
the three no-market baselines. No model got all six exact in any round.

## Findings

1. **Team-strength models clearly beat the simple controls.** Dixon–Coles is better than
   the league-average, rolling-goals and empirical baselines on every probabilistic metric,
   and on points per fixture in points mode (+0.09 to +0.13 points/fixture). Every 95%
   interval excludes zero, and the ordering holds in each of the four seasons.
2. **The betting market is better still.** De-vigged pre-match odds beat Dixon–Coles on log
   loss (−0.025, CI −0.031 to −0.020), RPS and Brier in every season. The points
   difference (+0.025/fixture) is not significant. This is the expected result: markets
   aggregate team news, injuries and line-ups that the results-only model does not see.
   The odds-timing rule used is conservative: about 2.7% of fixtures (bank-holiday Fridays
   and Tuesdays) had no usable odds and fell back to the league average. That handicaps the
   market, so if anything its lead is understated.
3. **The Dixon–Coles adjustment has not earned its place.** It is indistinguishable from
   plain Poisson on log loss, RPS and points mode. Its fitted ρ is small (≈ −0.02), and it
   is slightly worse on jackpot-mode points (−0.03, CI −0.05 to −0.01). With 30
   comparisons in the table, one marginal result is weak evidence either way. Per
   DESIGN §1, it should not be preferred over the simpler model on this evidence.
4. **Calibration is good.** Dixon–Coles home/draw/away probabilities track observed
   frequencies within about one standard error in every well-populated bin. Its jackpot
   pick is forecast at 12.9% on average and hit 13.2% of the time. Its expected points
   (1.301/fixture) match realised points (1.299/fixture). The rolling-goals baseline, by
   contrast, expected 1.40 but realised 1.21.
5. **Points mode works as designed.** It hits fewer exact scores than jackpot mode (11.1%
   vs 12.7%) but scores more points (1.30 vs 1.12 per fixture), because it backs the more
   likely result.

## Decisions and next steps

- The default model stays `dixon_coles` for now. It was the predeclared choice, and
  switching to Poisson because of this test window would be selection on the test set.
  Choose the champion on the next fresh period, which can be the live shadow period
  (DESIGN §13.5).
- Highest-value Phase 2 work: a **market + team-strength ensemble** with weights fitted on
  validation folds only, plus pre-deadline odds collection for live rounds. Then xG
  (available from 2026-27 in this source) as an ablation.
- Replace the uniform-hazard Golden Goal baseline once licensed goal-timing data exists.
- Verify the competition rules against the official terms, and set `verified: true`.

## Limitations

- These are match-level results on all league fixtures. Real Super 6 rounds favour
  televised and high-profile games, so round-level numbers are illustrative only.
- Odds and result availability times follow documented approximations
  (see `docs/DATA_DICTIONARY.md`), not observed timestamps.
- English and Scottish pools are not linked, so ratings are not comparable across them.
  Cross-pool and cup fixtures are not evaluated.
