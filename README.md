# Super 6 AI

A probabilistic football forecasting engine that recommends exact scores for the six
fixtures in a Sky Sports Super 6 round, plus a Golden Goal minute. It builds a full
probability distribution over scorelines for each match, then picks scores for two goals:

- **Points mode** (default): maximise expected Super 6 points (`3·P(score) + 2·P(result)`
  under the 5/2/0 table).
- **Jackpot mode**: maximise the chance that all six exact scores are right.

The full technical design is in [`DESIGN.md`](DESIGN.md). This repository currently
implements **Phase 1** (the statistical core): see [Status](#status).

> Forecasts are estimates, never guarantees. Even the most likely exact score usually has
> only a 10–15% chance, and six together are far less likely. This is an analysis tool for
> a free-to-play game. It does not place bets, and you submit entries yourself on the
> official site. It is not affiliated with or endorsed by Sky.

## Quick start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

super6 db init                 # SQLite at data/super6.db (set SUPER6_DATABASE_URL for Postgres)
super6 data download           # Football-Data.co.uk CSVs, 2016-17 → current season
super6 data ingest             # validate + load (idempotent)
super6 predict rounds/example-round.yaml
```

Example output for the **illustrative** round file (invented fixtures, not an official round):

```text
fixture                                     points  jackpot  P(jackpot pick)  E[pts]  H/D/A              xG h/a     basis
Arsenal v Newcastle United                  2-0     2-0      12.8%            1.75    68.4%/19.7%/11.9%  2.09/0.75  results model
Manchester United v Brighton & Hove Albion  1-2     1-1      11.6%            1.06    35.0%/25.0%/40.0%  1.43/1.54  results model
Coventry City v Fulham                      0-1     1-1      12.8%            1.04    35.2%/26.9%/37.9%  1.29/1.34  results model
...
expected points: points mode 7.77, jackpot mode 6.96
Golden Goal: minute 4 (80% interval 1–14, expected error ±3.9 min) [uniform-hazard-poisson-simulation — baseline]
```

Where the two modes differ, points mode is backing the more likely *result*: Brighton and
Fulham are slight favourites, so 1-2 and 0-1 beat 1-1 on expected points even though 1-1
is the single most likely score.

The example round's fixtures are invented, so there are no odds for them. Every row
therefore shows `basis: results model`, and the CLI prints a reminder to fetch odds.

## Which model, and how well it does

The default model is **`ensemble`**. It uses bookmaker odds collected before the cutoff
when they exist, and falls back to the results-based Dixon–Coles model when they don't.
Held-out backtest, 2023-24 → November 2026, 7,441 fixtures:

| Model | Exact-score log loss ↓ | Exact hit rate | Points/fixture |
|---|---:|---:|---:|
| **Ensemble (default)** | **2.881** | 13.0% | **1.33** |
| Market-implied only | 2.882 | 12.9% | 1.32 |
| Dixon–Coles only | 2.907 | 12.7% | 1.30 |
| Best simple baseline (best of three, per column) | 2.969 | 12.1% | 1.21 |

Write-ups: [experiment 001](docs/experiments/2026-11-05-phase1-walk-forward.md) (models
vs baselines) and [experiment 002](docs/experiments/2026-11-05-ensemble-market-first.md)
(the ensemble, with its promotion rule declared before the test).

With Docker and PostgreSQL instead: `cp .env.example .env`, set a password, then follow the
commands at the top of [`docker-compose.yml`](docker-compose.yml).

## Weekly workflow

**Unattended:** schedule `super6 cycle` hourly (cron or `docker compose up -d scheduler`).
It fetches the round, refreshes results and odds, re-forecasts when they change, locks
the forecast an hour before the deadline, settles after the matches, and alerts on
anything that goes wrong. See `docs/OPERATIONS.md`. You still enter the picks in the app
yourself.

**By hand**, step by step:

1. **Refresh data**: `super6 data download && super6 data ingest`.
2. **Get pre-deadline odds**: `super6 data upcoming`, once the provider lists the
   weekend's fixtures (usually by Friday). It also prints their UTC kick-off times.
   Without odds, the forecast falls back to the weaker results-only model.
   You can also type odds into the round file (`odds:`; see the example).
3. **Get the round**: `super6 round fetch --round 16` finds the six fixtures in Sky
   Sports' public preview article, from headings marked "PLAY SUPER 6!". It checks
   robots.txt and identifies itself honestly. If it can't find exactly six, copy them from
   the app instead:
   `super6 round import "Fulham v Newcastle, Man Utd v Aston Villa, ..." --round 16`.
   It fills in competitions and kick-off times from the provider's fixture list, sets the
   deadline to the first kick-off, writes `rounds/<round-id>.yaml`, and runs the
   prediction. Check any kick-off it marks as assumed. You can also write the round file
   by hand from `rounds/example-round.yaml`.
4. **Predict**: `super6 predict rounds/<round>.yaml --json reports/<round>.json`. The run is
   stored with its inputs fingerprint, model version and rules version. It is `BLOCKED` if
   the fixtures, rules or probabilities fail checks.
5. **Lock** the run you are going to use, before the deadline: `super6 runs lock <run-id>`.
   A locked run cannot be edited or deleted.
6. **Enter the picks yourself** on the official Super 6 site.
7. **Settle** after the matches: `super6 data download && super6 data ingest && super6 settle <round-id>`.

To test the model on real past rounds, add verified round files to `rounds/history/` and
run `super6 replay-rounds rounds/history` (see `docs/OPERATIONS.md`).

Other commands: `super6 runs list`, `super6 runs show <id>`, `super6 ratings --competition E0`,
`super6 backtest --start 2023-07-01 --end 2026-11-05`, and `super6 --help`.

## What is implemented

| Area | Module | Notes |
|---|---|---|
| Competition rules | `configs/competition_rules.yaml`, `rules.py` | Strict schema; unknown or changed settings block predictions. Checked against the 2026/27 terms; see [`docs/RULES.md`](docs/RULES.md) |
| Ingestion | `ingestion/` | Football-Data.co.uk adapter; raw files kept with checksum and retrieval time; validation and quarantine; alias review warnings |
| Storage | `storage/`, `migrations/` | DESIGN §14 schema, Alembic migrations (SQLite + PostgreSQL), immutable locked runs |
| Point-in-time data | `features/point_in_time.py` | The only path from history to models: results published and odds collected before the cutoff |
| Models | `models/` | Market-first ensemble (default), Dixon–Coles, team-strength Poisson, market-implied, and baselines: league average, empirical scorelines, rolling goals |
| Score grid | `prediction/score_grid.py` | Adaptive grid with explicit tail mass; never silently renormalised |
| Optimiser | `prediction/optimiser.py` | Jackpot and points modes with deterministic tie-breaks |
| Golden Goal | `golden_goal/` | 1-indexed minutes, staggered kick-offs, closest-minute median. A **labelled baseline** until goal-timing data is licensed |
| Evaluation | `evaluation/` | Walk-forward replay, paired block bootstrap, calibration, synthetic rounds (labelled as such) |

## Status

Phase 1 of the [roadmap](DESIGN.md#20-delivery-roadmap) is in place, and most of Phase 2:

- the market-first ensemble (the default);
- point-in-time ML features (Elo, goals, shots, xG, schedule, tier);
- CatBoost challengers;
- feature ablations;
- replay of verified historical rounds.

Phase 2 is complete apart from the xG evaluation, which has too little xG history for now.
The work also added a leak-free calibration layer, experiment tracking
(`super6 experiments`) and a residual-dependence check (DESIGN "Model C"). Five
experiments, each with rules declared in advance, are indexed in
[`docs/experiments/`](docs/experiments/README.md). The default is still the market-first
ensemble: every challenger was tested and rejected. Not yet built:

- xG evaluation, which needs more xG history (see the experiment index).
- Licensed goal-timing data, a hazard model for Golden Goal, scheduling and alerting
  (Phase 3).
- FastAPI service and Next.js dashboard (Phase 4). The JSON written by
  `predict --json` already follows the API response shape in DESIGN §15.1.

## Development

```bash
ruff check src tests scripts migrations
mypy                      # strict
pytest                    # unit, leakage, integration
SUPER6_TEST_POSTGRES_URL=postgresql+psycopg://user:pass@localhost/db pytest -m postgres
```

See [`docs/OPERATIONS.md`](docs/OPERATIONS.md), [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md)
and [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md).

## Data and attribution

Historical results and odds courtesy of [Football-Data.co.uk](https://www.football-data.co.uk/).
Raw files are downloaded locally into `data/` and are not committed or redistributed. Review
each provider's terms before any production or commercial use (`configs/data_sources.yaml`).
Super 6, Sky Sports and Sky Bet are third-party marks.

## Licence

MIT. See [`LICENSE`](LICENSE).
