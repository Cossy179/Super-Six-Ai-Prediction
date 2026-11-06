# Experiment 006: when goals happen, for the Golden Goal

- **Date:** 6 November 2026
- **Status:** protocol and adoption rule committed *before* any test-window result was
  computed. Results are added below.

## Why (DESIGN §12)

The Golden Goal forecast has so far assumed a constant scoring rate through each match
(`uniform-hazard`), with 2 and 5 minutes of stoppage time. Real goals are not spread
evenly: scoring tends to rise through a match, and stoppage-time goals, which the official
rules record as minute 45 or 90, are common. openfootball (CC0) now provides goal minutes
for every 2025-26 Premier League match. `empirical-hazard` is a goal-time profile fitted
to those minutes: shares by 5-minute band within each half, plus each half's stoppage
share, scaled by each match's expected goals.

### Disclosure

Before writing this protocol, while checking the ingested data, I looked at two
*whole-season* 2025-26 summaries. Both include the test window below:

- stoppage-time goals were 14% of all goals (43 first half, 104 second half), against
  about 7% under the uniform baseline;
- the minute of each match's first goal, in 15-minute bands.

So the test window is not untouched in the strictest sense: I already knew the direction
of the main effect. No model was fitted, tuned or scored on it. The profile's settings
(5-minute bands; 3 and 7 stoppage cells) are fixed below and will not change after the
results are seen.

## Data

- **Goal minutes:** openfootball `1-premierleague.txt`, 2025-26, as ingested by
  `super6 data goals` (E0 only; 1045 goals in 380 matches). A match counts as covered if
  it is 0-0, or its goal events match its score exactly. Uncovered matches are excluded
  and counted.
- **Expected goals per match:** λ_home + λ_away from the `ensemble` rows of
  `reports/exp003-reused/predictions.csv.gz`. These are walk-forward, pre-kick-off
  forecasts (experiment 003's reused 2023–26 window), so no new score-model fitting is
  needed.
- **Recorded minute** of a goal, under the official rules (capped stoppage time,
  1-indexed): the match minute, with first-half stoppage recorded as 45 and second-half
  stoppage as 90.

## Arms

- **Baseline:** `uniform_profile(2, 5)`, the current default.
- **Candidate:** `empirical_profile(goals, bin_minutes=5, first_half_stoppage=3,
  second_half_stoppage=7)`.
- **Walk-forward fitting:** for each test match, the candidate is fitted on the goals of
  2025-26 E0 matches whose results were public before that match's forecast cutoff
  (kick-off + 135 minutes, as for results elsewhere). For each synthetic round, it is
  fitted on goals public before the round's first kick-off. When fewer than 400 goals
  are available, the candidate falls back to the baseline. This should not happen in the
  test window.

## Test window

E0 matches kicking off **2026-01-01 → 2026-06-30** that are covered and have an
`ensemble` forecast.

## Metrics

**(a) Match-level first-goal log loss (primary).** Each match's first recorded goal minute
falls into one of 21 categories:

- 1–5, 6–10, …, 36–40, then 41–44, then 45;
- 46–50, …, 81–85, then 86–89, then 90;
- no goal.

The score is the mean log loss of the probability each arm gives to the observed
category. Bands 41–44 and 86–89 keep the stoppage minutes (45, 90) as their own
categories, where the two profiles differ most.

The result is the difference, candidate minus baseline, with a 95% paired block-bootstrap
interval. The blocks are ISO weeks, with 2000 resamples and seed 7.

**(b) Golden Goal pick error (guard).** Synthetic six-fixture rounds:

- For each ISO week in the window, the six earliest-kicking-off covered matches are used
  (ties broken by fixture ID). Weeks with fewer than six such matches are skipped.
- Each arm picks the Golden Goal with `simulate_golden_goal` under the official 2026/27
  rules: earliest match minute, closest-minute median pick, 0 for a goalless round.
- The actual answer is the lowest recorded first-goal minute among the six, or 0 if none
  were scored.
- The result is the mean absolute error difference, candidate minus baseline, with a 95%
  paired bootstrap over rounds (2000 resamples, seed 7).

Each fixture's λ comes from its own pre-kick-off forecast, which for a Sunday match can
include Saturday's results. This applies equally to both arms.

**Also reported:**

- each arm's forecast share of first goals in minutes 45 and 90 against the observed
  share;
- the mean forecast of the round's expected absolute error.

## Adoption rule

Set `golden_goal.profile: empirical` as the default **only if both** hold:

1. In (a), the candidate's log loss is lower, and the 95% interval of the difference lies
   entirely below zero.
2. In (b), the candidate is not significantly worse: the lower end of the 95% interval of
   the absolute-error difference is ≤ 0.

Otherwise `uniform` stays the default and the empirical profile remains an option.

## Command

```bash
python scripts/golden_goal_experiment.py \
  --predictions reports/exp003-reused/predictions.csv.gz \
  --start 2026-01-01 --end 2026-07-01 --out reports/exp006
```

## Results

*To be added after the run.*
