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

## Running unattended: `super6 cycle`

Run `super6 cycle` every hour. It is idempotent and does whatever the clock says is due:

| When | What it does |
|---|---|
| Every run | Refreshes data: current-season results at most every 6 h, upcoming fixtures and odds at most hourly |
| Up to 72 h before Saturday, no round file covers the weekend | `round fetch` from public previews. Alerts `ROUND_NOT_FOUND` if fewer than six are found |
| Before the deadline | Aligns kick-offs with the provider (`KICKOFF_CHANGED` if a game moved; the round file and the deadline are updated). Re-forecasts, saving and publishing a new run only if a pick, the Golden Goal minute, or a probability changed (by > 0.1 points), e.g. when odds arrive |
| Last 60 min before the deadline | Locks the latest run once (`ROUND_LOCKED`, with the picks) |
| After the deadline | `ROUND_NOT_LOCKED` if nothing was locked. Settles once results are in (`ROUND_SETTLED`); a fixture with no result 3 days after kick-off is void (`VOID_FIXTURES`) |

Scheduling, pick one:

```bash
# cron (on the machine that keeps the database)
5 * * * * cd /path/to/Super-Six-Ai-Prediction && .venv/bin/super6 cycle >> reports/cycle.log 2>&1

# or Docker Compose (PostgreSQL + an hourly loop)
docker compose up -d db scheduler
```

The schedule must run where the database persists. A fresh cloud container each time
would lose the locked records.

**Alerts** go to `reports/alerts.jsonl`, and the command exits 1 if any error was raised.
Warnings and errors are also posted to `SUPER6_ALERT_WEBHOOK_URL` (Slack or Discord
incoming webhook) when it is set. The same alert for the same round is sent at most once
a day. Each run's summary is written to `reports/cycle/`.

**Golden Goal evaluation.** Settlement works out the Golden Goal from stored goal minutes
when every fixture is covered, meaning it ended 0-0 or its goal minutes match its score.
openfootball (CC0) usually publishes goal minutes a few days after a round. The cycle
does not revisit a settled round, so once they appear run:

```bash
super6 data goals && super6 settle <round-id>
```

Alternatively, record the official minute from the app:
`super6 settle <round-id> --golden-goal-minute 7` (use 0 if there were no goals). An
official minute always replaces one worked out from goal minutes. It is also the only
way to settle under the `first_in_real_time` reading, which needs wall-clock goal times.
Each run's Golden Goal error is stored either way.

## Before each round

1. `super6 data download && super6 data ingest`, then `super6 data status`. Check the
   latest result dates and any new quality issues.
2. Check the official rules if a new season's terms have appeared or the operator has
   announced a change. Compare them with `docs/RULES.md`, update
   `configs/competition_rules.yaml`, and bump `rules_version` (round files must match it).
3. Write the round file from the official fixture list and deadline.
4. `super6 predict …`. Read the warnings:
   - `RESULTS_STALE` / `TEAM_DATA_STALE`: refresh the data first.
   - `UNKNOWN_TEAM` / `LOW_DATA_TEAM`: a team with little or no history (newly promoted
     from outside the ingested leagues). Its forecast is shrunk to an average team.
   - `GOLDEN_GOAL_BASELINE_ONLY`: the minute comes from the uniform-hazard profile (the
     default; see experiment 006).
   - `GOLDEN_GOAL_PROFILE_FALLBACK`: `profile: empirical` was configured but fewer than
     400 goal minutes were public at the cutoff, so the uniform profile was used. Run
     `super6 data goals`.
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

## Getting each round's fixtures

1. `super6 round fetch --round N` reads Sky Sports' public news listing pages, follows this
   week's predictions articles, and extracts the headings marked "PLAY SUPER 6!".
   - It sends an honest User-Agent, checks robots.txt for every URL, leaves at least 2 s
     between requests, and fetches a handful of pages per run.
   - Blocked or disallowed pages are skipped, never worked around.
   - Raw pages are kept in `data/raw/round_sources/` (gitignored) for provenance.
2. If it doesn't find exactly six, nothing is written. Paste the six from the app with
   `super6 round import "Home v Away, ..."`.
3. Either way, the round is checked: team names must resolve, there must be six fixtures,
   and the deadline is the first kick-off. The round file records where the fixtures
   came from; open it and compare with the app.

Never log in to the Super 6 site or call its internal API from the tool; there is no
authorised access.

## Replaying real Super 6 rounds

Add verified historical rounds as round files with `illustrative: false`, for example in
`rounds/history/<round-id>.yaml`. Each needs the six fixtures the competition actually
used and its real deadline. Then run:

```bash
super6 replay-rounds rounds/history --out reports/history-replay.csv
```

Each round is forecast one minute before its deadline, scored against ingested results,
and labelled `historical`. Rounds marked illustrative are skipped unless you pass
`--allow-illustrative`. Rounds that fail validation are reported as BLOCKED.

## Backtests and model changes

- Tune only on a validation window (`scripts/tune_validation.py`), then run
  `super6 backtest` on a later, untouched window.
- A model change counts as an improvement only if the paired bootstrap interval excludes
  zero on the predeclared metrics, and the gain repeats across seasons.
- Record each experiment in `docs/experiments/`.
