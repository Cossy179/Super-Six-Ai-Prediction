# Roadmap status (DESIGN.md §20)

## Phase 1: data foundation and statistical MVP ✓

A reproducible CLI that takes six fixtures and produces an auditable recommendation set. It
uses no future information, its probabilities pass the integrity checks, and it is
compared against baselines in [experiment 001](experiments/2026-11-05-phase1-walk-forward.md).

## Phase 2: enriched modelling and evaluation ✓ (xG deferred)

- **Champion:** the market-first ensemble, selected on out-of-sample evidence.
- **Experiments:** five, all with rules declared in advance, covering ablations,
  calibration and the Model C check. See the [experiment index](experiments/README.md),
  which also has the exit-criteria table.
- **xG:** waiting for enough xG history.

## Phase 3: Golden Goal and round automation (in progress)

| Build item | Status |
|---|---|
| Rule and fixture checks | ✓ Official 2026/27 rules ([RULES.md](RULES.md)); round validation (six fixtures, deadline = first kick-off, teams, competitions) |
| Fixture import | ✓ `super6 round fetch` (public previews) and `round import` (paste) |
| Scheduling | ✓ `super6 cycle` hourly, via cron or the Compose `scheduler` service |
| Prediction locking | ✓ Automatic in the last 60 minutes, with database-enforced immutability |
| Official settlement | ✓ Points, exact hits and log loss; void fixtures per the rules; Golden Goal error once you enter the official minute |
| Alerting | ✓ `reports/alerts.jsonl`, exit code, optional Slack/Discord webhook |
| Delayed kick-offs | ✓ Provider kick-off changes update the round and the deadline, with an alert |
| Golden Goal: historical baseline | ⧗ Needs historical first-goal minutes. The current pick is a labelled uniform-hazard simulation |
| Golden Goal: hazard model and simulation challenger | ⧗ Blocked: needs licensed goal-event times (DESIGN §12.3) |
| Authorised goal-event ingestion | ⧗ Blocked: no licensed source yet. `goal_events` table is ready |

| Exit criterion | Status |
|---|---|
| Valid six-fixture output | ✓ |
| Accurate tie-break-minute conversion | ✓ Tested against the official minute rules, including injury time recorded as 45/90 |
| Delayed kick-offs and no-goal cases | ✓ Kick-off alignment; goalless rounds count as minute 0 |
| Failures visibly reported | ✓ Alerts, exit code and webhook |

**To finish Phase 3:** licence a goal-event feed, or collect official Golden Goal minutes
over the shadow period with `settle --golden-goal-minute`. Then build and test the hazard
model under a predeclared rule, like the other experiments.

## Phase 4: dashboard and production readiness (not started)

FastAPI service (DESIGN §15), Next.js dashboard, explanations, authenticated admin, and
deployment.
