# Data dictionary

All timestamps are stored as timezone-aware UTC. Local (UK) time is used only to parse
provider data and for display.

## Identifiers

| Entity | Format | Example |
|---|---|---|
| Competition | Football-Data.co.uk division code | `E0` (Premier League), `SC0` |
| Team | slug of the Football-Data.co.uk name | `nottm-forest`, `man-united` |
| Fixture | `{competition}-{YYYYMMDD local}-{home}-{away}` | `E0-20261107-arsenal-newcastle` |
| Prediction run | `run-` + first 16 hex chars of the input fingerprint | `run-a576807c68795d33` |

The date is part of the fixture ID because some leagues (e.g. the Scottish Premiership)
schedule the same home/away pairing more than once a season. A fixture moved to a new
date gets a new ID; the old scheduled row stays without a result and is never used for
training.

## Timing rules (point-in-time integrity)

| Field | Rule | Why |
|---|---|---|
| `fixtures.kickoff_utc` | UK local kick-off → UTC. Missing times (seasons before 2019-20) → 15:00 local, `kickoff_time_known = false` | Provider times are UK local |
| `fixture_results.result_available_at` | kick-off + 135 min, or 23:59:59 local on the match day when the kick-off time is unknown | A result can only inform forecasts made after it was public |
| `market_snapshots.available_at` (prematch) | 18:00 UK on Friday (Fri–Mon games) or Tuesday (Tue–Thu games), or one hour before kick-off if earlier. For upcoming fixtures, the download time | Provider's published collection schedule; chosen to be late rather than early |
| `market_snapshots.available_at` (closing) | kick-off | Closing odds are never available before the deadline |
| `fixture_stats.available_at` | same as the result | Shots and xG are post-match information |

Models only read data through `PointInTimeView(cutoff)`, which filters on these fields.

## Tables

See `src/super6/storage/models.py` for column types. Key tables:

- `source_snapshots`: one row per downloaded file version (`uri`, `checksum`,
  `retrieved_at`, quality status). Re-ingesting an unchanged file is a no-op.
- `data_quality_issues`: rejected/quarantined rows and warnings, such as
  `RESULT_CONFLICT` (a later file disagrees with a stored result; the original is kept)
  and `POSSIBLE_DUPLICATE_TEAM` (a new team name resembles an existing one).
- `fixtures`, `fixture_results`, `market_snapshots`, `fixture_stats`, `goal_events`
  (empty until a licensed event feed exists).
- `super6_rounds`, `super6_round_fixtures`: the manually verified round.
- `prediction_runs`: lifecycle `DRAFT → VALIDATED → PUBLISHED → LOCKED → SETTLED`, or
  `BLOCKED`. Holds the input fingerprint, code version, rules version, warnings and
  block reasons.
- `fixture_forecasts`, `score_probabilities` (the full grid), `recommendations` (both
  modes), `golden_goal_predictions`: the run body. Immutable once the run is locked.
- `model_registry`, `feature_snapshots`: model config and training-data fingerprints.
- `evaluations`: per-run points, exact hits, log loss and RPS after settlement.

## Match statistics coverage (Football-Data.co.uk)

| Statistic | Coverage in the ingested data |
|---|---|
| Shots, shots on target | E0–E3 and SC0, every season from 2016-17. SC1 from 2017-18. **None for the National League (EC) before 2026-27.** |
| xG (`HxG`, `AxG`) | **2026-27 only**, for E0–E3 and SC0 |

Missing statistics stay missing (`NaN`). Feature averages carry a count of the matches
they are based on, so a team promoted from the National League has shot features built
only from its EFL matches. Because xG has under one season of history, it can't yet be
trained on or evaluated.

## ML features (`features/team_features.py`)

Features are computed by an event-driven builder. Each result becomes visible at its
`result_available_at`, and each feature request is answered as of a given time: one minute
before kick-off for training rows, and the prediction cutoff for live and replayed
forecasts. Families: `elo`, `goals`, `shots`, `xg`, `schedule`, `tier`. Market inputs,
for `catboost_market`, are de-vigged pre-match probabilities available before each row's
own kick-off; closing odds are never used.

## Known provider quirks (reviewed)

- 2026-27 E1: one row names "Sheffield Utd" while all others use "Sheffield United".
- 2026-27 E2: one row names "Oxford Utd" while all others use "Oxford".
- 2019-20: Premier League and Championship seasons ran into July/August 2020.

Both renames are mapped in `configs/team_aliases.yaml`.
