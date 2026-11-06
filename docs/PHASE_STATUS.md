# Roadmap status (DESIGN.md §20)

## Phase 1: data foundation and statistical MVP ✓

A reproducible CLI that takes six fixtures and produces an auditable recommendation set. It
uses no future information, its probabilities pass the integrity checks, and it is
compared against baselines in [experiment 001](experiments/2026-11-05-phase1-walk-forward.md).

## Phase 2: enriched modelling and evaluation ✓ (xG deferred)

- **Champion:** the market-first ensemble, selected on out-of-sample evidence.
- **Experiments:** five (001–005), all with rules declared in advance, covering ablations,
  calibration and the Model C check. See the [experiment index](experiments/README.md),
  which also has the exit-criteria table.
- **xG:** waiting for enough xG history.

## Phase 3: Golden Goal and round automation ✓

| Build item | Status |
|---|---|
| Rule and fixture checks | ✓ Official 2026/27 rules ([RULES.md](RULES.md)); round validation (six fixtures, deadline = first kick-off, teams, competitions) |
| Fixture import | ✓ `super6 round fetch` (public previews) and `round import` (paste) |
| Scheduling | ✓ `super6 cycle` hourly, via cron or the Compose `scheduler` service |
| Prediction locking | ✓ Automatic in the last 60 minutes, with database-enforced immutability |
| Official settlement | ✓ Points, exact hits and log loss; void fixtures per the rules; the Golden Goal is worked out from stored goal minutes, or from the official minute you enter (which takes precedence) |
| Alerting | ✓ `reports/alerts.jsonl`, exit code, optional Slack/Discord webhook |
| Delayed kick-offs | ✓ Provider kick-off changes update the round and the deadline, with an alert |
| Goal-event ingestion | ✓ `super6 data goals`: Premier League goal minutes from openfootball (CC0, from 2025-26), checked against stored scores and point-in-time like results |
| Golden Goal: historical baseline | ✓ Uniform-hazard profile scaled by each match's expected goals; the earliest-minute answer is computed exactly |
| Golden Goal: hazard model and simulation challenger | ✓ Empirical goal-time profile (`profile: empirical`) and real-time Monte Carlo. [Experiment 006](experiments/2026-11-06-golden-goal-profile.md): not promoted, so uniform stays the default |

| Exit criterion | Status |
|---|---|
| Valid six-fixture output | ✓ |
| Accurate tie-break-minute conversion | ✓ Tested against the official minute rules, including injury time recorded as 45/90 |
| Delayed kick-offs and no-goal cases | ✓ Kick-off alignment; goalless rounds count as minute 0 |
| Failures visibly reported | ✓ Alerts, exit code and webhook |

**Open after Phase 3:**

- A smoothed goal-time profile (experiment 006's lead), tested on 2026-27 goal minutes
  once openfootball publishes them.
- Championship goal minutes: openfootball has none yet.
- The real-time Golden Goal reading cannot be settled from stored data, because it needs
  wall-clock goal times. Under that reading, enter the official minute with
  `settle --golden-goal-minute`.

## Phase 4: dashboard and production readiness (not started)

FastAPI service (DESIGN §15), Next.js dashboard, explanations, authenticated admin, and
deployment.
