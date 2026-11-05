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
fixture                                     points  jackpot  P(jackpot pick)  E[pts]  H/D/A              xG h/a
Arsenal v Newcastle United                  2-0     2-0      12.5%            1.73    67.6%/19.9%/12.4%  2.08/0.77
Manchester United v Brighton & Hove Albion  1-2     1-1      11.6%            1.05    35.3%/25.0%/39.7%  1.43/1.53
...
Golden Goal: minute 4 (80% interval 1–13, expected error ±3.88 min) [uniform-hazard-poisson-simulation — baseline]
```

With Docker and PostgreSQL instead: `cp .env.example .env`, set a password, then follow the
commands at the top of [`docker-compose.yml`](docker-compose.yml).

## Weekly workflow

1. **Refresh data**: `super6 data download && super6 data ingest`.
2. **Write the round file.** Copy `rounds/example-round.yaml`, set `illustrative: false`,
   and enter the official six fixtures, kick-offs and deadline from the Super 6 site.
   `super6 data upcoming` lists fixtures the data provider has published, with their
   UTC kick-off times.
3. **Predict**: `super6 predict rounds/<round>.yaml --json reports/<round>.json`. The run is
   stored with its inputs fingerprint, model version and rules version. It is `BLOCKED` if
   the fixtures, rules or probabilities fail checks.
4. **Lock** the run you are going to use, before the deadline: `super6 runs lock <run-id>`.
   A locked run cannot be edited or deleted.
5. **Enter the picks yourself** on the official Super 6 site.
6. **Settle** after the matches: `super6 data download && super6 data ingest && super6 settle <round-id>`.

Other commands: `super6 runs list`, `super6 runs show <id>`, `super6 ratings --competition E0`,
`super6 backtest --start 2023-07-01 --end 2026-11-05`, and `super6 --help`.

## What is implemented

| Area | Module | Notes |
|---|---|---|
| Competition rules | `configs/competition_rules.yaml`, `rules.py` | Strict schema; unknown or changed settings block predictions. **Not yet verified** against official terms. |
| Ingestion | `ingestion/` | Football-Data.co.uk adapter; raw files kept with checksum and retrieval time; validation and quarantine; alias review warnings |
| Storage | `storage/`, `migrations/` | DESIGN §14 schema, Alembic migrations (SQLite + PostgreSQL), immutable locked runs |
| Point-in-time data | `features/point_in_time.py` | The only path from history to models: results published and odds collected before the cutoff |
| Models | `models/` | Dixon–Coles (default), team-strength Poisson, and baselines: league average, empirical scorelines, rolling goals, market-implied |
| Score grid | `prediction/score_grid.py` | Adaptive grid with explicit tail mass; never silently renormalised |
| Optimiser | `prediction/optimiser.py` | Jackpot and points modes with deterministic tie-breaks |
| Golden Goal | `golden_goal/` | 1-indexed minutes, staggered kick-offs, closest-minute median. A **labelled baseline** until goal-timing data is licensed |
| Evaluation | `evaluation/` | Walk-forward replay, paired block bootstrap, calibration, synthetic rounds (labelled as such) |

## Status

Phase 1 of the [roadmap](DESIGN.md#20-delivery-roadmap) is in place. The first held-out
backtest is written up in [`docs/experiments/`](docs/experiments/). Not yet built:

- xG and other enriched features, a CatBoost challenger, ensembles and calibration layers
  (Phase 2). xG columns are already ingested where the provider supplies them (2026-27).
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
