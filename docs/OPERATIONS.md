# Operations

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SUPER6_DATABASE_URL` | `sqlite:///data/super6.db` | SQLAlchemy URL; use `postgresql+psycopg://…` in deployment |
| `SUPER6_DATA_DIR` | `data/` | Raw downloads (`data/raw/football_data_couk/<season>/<code>.csv` + `.meta.json`) |
| `SUPER6_REPORTS_DIR` | `reports/` | Backtest output |
| `SUPER6_HOME` | auto-detected | Directory containing `configs/` |

Secrets (database passwords, future API keys) belong in `.env` or a secrets manager,
never in the repository.

## Before each round

1. `super6 data download && super6 data ingest`, then `super6 data status`. Check the
   latest result dates and any new quality issues.
2. Check the official rules. If anything changed, update `configs/competition_rules.yaml`
   and bump `rules_version`. When the rules have been checked against the operator's
   terms, set `verified: true` and `verified_on`.
3. Write the round file from the official fixture list and deadline.
4. `super6 predict …`. Read the warnings:
   - `RESULTS_STALE` / `TEAM_DATA_STALE`: refresh the data first.
   - `UNKNOWN_TEAM` / `LOW_DATA_TEAM`: a team with little or no history (newly promoted
     from outside the ingested leagues). Its forecast is shrunk to an average team.
   - `GOLDEN_GOAL_BASELINE_ONLY`: the minute comes from the uniform-hazard baseline.
5. `super6 runs lock <run-id>` before the deadline.

## Failure behaviour

- Download errors on HTTP 429/5xx are retried with exponential backoff. Other errors skip
  the file and are reported.
- An unchanged file (same checksum) is not re-ingested. A changed file creates a new
  snapshot: new rows are added, and conflicting results are recorded as issues while the
  original is kept.
- A round is BLOCKED (and the CLI exits with status 2) for: wrong fixture count, rules
  version mismatch, unknown team, unconfigured competition, a team in two fixtures, a
  deadline after the first kick-off, a cutoff after the deadline, or a probability
  integrity failure.
- Locking is refused after the deadline, for a second run in the same round, or for a run
  that is not VALIDATED/PUBLISHED.

## Backtests and model changes

- Tune only on a validation window (`scripts/tune_validation.py`), then run
  `super6 backtest` on a later, untouched window.
- A model change counts as an improvement only if the paired bootstrap interval excludes
  zero on the predeclared metrics, and the gain repeats across seasons.
- Record each experiment in `docs/experiments/`.
