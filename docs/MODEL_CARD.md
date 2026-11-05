# Model card: `ensemble` (default), `dixon_coles` and `poisson`

## Default: `ensemble` (market first, results-model fallback)

For each fixture, the ensemble uses the market-implied distribution when bookmaker odds
were collected before the cutoff. Otherwise it uses the Dixon–Coles distribution described
below, flagged `ENSEMBLE_TEAM_MODEL_ONLY` and shown as `basis: results model`.

- **Market component:** the bookmaker margin is removed proportionally. Independent-Poisson
  goal rates are then solved to match the home/draw/away probabilities and, where quoted,
  the over/under 2.5 goals probability. Odds older than four days are flagged
  `MARKET_ODDS_STALE`.
- **Weight:** `market_weight = 1.0`, fitted on the 2022-23 validation season. Log loss
  improved steadily as the market weight rose, under both an arithmetic mixture and a
  log-rate blend (experiment 002).
- **Held out, 2023-07 → 2026-11:** exact-score log loss 2.881 vs 2.907 for Dixon–Coles,
  and points per fixture 1.326 vs 1.299. Both differences have 95% intervals that exclude
  zero. It was promoted by a rule declared before the test.
- **Dependency:** the advantage exists only when pre-deadline odds are available. Run
  `super6 data upcoming` before predicting, or enter odds in the round file.

The rest of this card describes the results-based team-strength models, which are used
for fixtures without odds and as the baseline the ensemble must beat.

## Purpose

These models estimate a full exact-score probability distribution for a domestic league
football match from results published before a cutoff. The Super 6 optimiser uses the
distribution to pick scores. The models are not for betting, staking or any claim of
guaranteed outcomes.

## Model

For home team *h* and away team *a* in competition *c*:

```
log λ_home = μ + home_c + att_h − def_a        log λ_away = μ + att_a − def_h
```

- Fitted by maximum likelihood with exponential time-decay weights
  `exp(−0.0019 · age_days)` over the last 1,095 days. The decay is about a one-year
  half-life.
- An L2 penalty (`ridge = 0.5`) on attack/defence identifies the model and shrinks
  low-data teams towards average.
- Home advantage is estimated per competition. All competitions in a pool (England E0–EC,
  Scotland SC0–SC1) are fitted jointly, so promoted and relegated teams carry their
  strength across divisions.
- `dixon_coles` adds the low-score dependence parameter ρ, bounded to [−0.2, 0.1] and
  shrunk per fixture if needed to keep probabilities non-negative.
- Output: a score grid whose excluded tail mass is below 10⁻⁶, with the tail reported
  explicitly.

Hyperparameters were chosen on the 2022-23 validation season only
([experiment 001](experiments/2026-11-05-phase1-walk-forward.md)).

## Inputs

Only full-time scores, dates, kick-off times, competitions and team identities, all
filtered through `PointInTimeView`. The model does not use xG, injuries, line-ups,
suspensions, weather or odds.

## Performance (held out, 2023-07 → 2026-11, 7,441 fixtures)

| Metric | Dixon–Coles | Best non-market baseline | Market-implied |
|---|---:|---:|---:|
| Exact-score log loss | 2.907 | 2.969 (league average) | 2.882 |
| Ranked probability score | 0.2138 | 0.2254 (rolling goals) | 0.2098 |
| Exact hit rate, jackpot picks | 12.7% | 12.1% (league average) | 12.9% |
| Points per fixture, points mode | 1.299 | 1.213 (rolling goals) | 1.323 |

Calibration of home/draw/away probabilities and of the jackpot-pick probability is good;
see the experiment record. Plain `poisson` performs the same as `dixon_coles`.

## Known limitations

- **The market is better.** Bookmaker odds carry information the model cannot see. That
  is why the default ensemble uses odds whenever they exist.
- **Information gaps.** Teams whose situation changed recently (new manager, injuries,
  transfer window) are judged only on their weighted past results.
- **Newly promoted teams** from outside the ingested leagues start at an average rating and
  are flagged `UNKNOWN_TEAM` or `LOW_DATA_TEAM`.
- **Pools are separate.** English and Scottish ratings are not on a common scale. Cup ties
  between pools, and neutral venues, are not validated.
- **Joint probability.** The six-fixture joint probability assumes independent matches
  and ignores parameter uncertainty.

## Golden Goal

The Golden Goal minute comes from a **labelled baseline** (`uniform-hazard-poisson-simulation`).
It assumes each match's goals arrive at a constant rate and uses the real kick-off times.
It has not been evaluated, because no licensed goal-timing data is ingested. Treat it as
a rough guide only.
