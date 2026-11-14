# REST API

The API serves what the prediction service has already stored. A `GET` never fits a model
or changes a forecast, so a locked run always reads back exactly as it was locked.

```bash
pip install -e ".[api]"
super6 serve                    # http://127.0.0.1:8000
# or: docker compose up -d api
```

Interactive documentation, generated from the code, is at `/api/docs`. The machine-readable
schema is at `/api/openapi.json`.

## Read endpoints (no token)

| Method and path | Returns |
|---|---|
| `GET /api/v1/health` | Database, data freshness, current round, scheduler, recent alerts, source attributions. 503 if the database is unreachable |
| `GET /api/v1/rounds` | Stored rounds, newest first (the archive) |
| `GET /api/v1/rounds/current` | The next round by deadline, or the most recent one |
| `GET /api/v1/rounds/{id}` | Fixtures, results, confirmation, and every forecast run |
| `GET /api/v1/rounds/{id}/predictions` | Both pick modes and the Golden Goal for the round's official run |
| `GET /api/v1/fixtures/{id}/distribution` | The full score grid and home/draw/away probabilities |
| `GET /api/v1/fixtures/{id}/explanation` | Odds used, model, recent results, and why each pick was made |
| `GET /api/v1/performance` | Live settled rounds and logged backtests, kept separate |
| `GET /api/v1/models` | The default model, its components, challengers and baselines |

The three forecast endpoints take an optional `?run_id=` to read a specific run.

### Which run is "official"

For a round: its locked (or settled) run if it has one. Otherwise the latest published run,
then the latest validated one.

**Check `is_locked` before relying on a forecast.** `false` means the picks can still
change, for example when odds arrive. Only a locked run is the record of what was forecast
before the deadline.

### Live performance: locked and unlocked

`/performance` marks each settled round as `locked` or `unlocked`. An unlocked round had no
forecast locked before its deadline, so its score is indicative only. Backtests are listed
separately and are never mixed into the live totals.

## Admin endpoints (token required)

Admin endpoints are **off** unless `SUPER6_ADMIN_TOKEN` is set (at least 24 characters).
Generate one with `super6 admin-token` and keep it in `.env` or a secrets manager.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/predictions/generate \
  -H "Authorization: Bearer $SUPER6_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"round_id": "super6-2026-27-round-17"}'
```

| Method and path | Does |
|---|---|
| `POST /api/v1/admin/rounds/import` | Builds a round file from the six matchups in the app. `confirmed: true` only if they were checked against the app |
| `POST /api/v1/admin/rounds/{id}/confirm` | Records when and how the six fixtures were checked |
| `POST /api/v1/admin/predictions/generate` | Forecasts the round. Idempotent (see below) |
| `POST /api/v1/admin/runs/{id}/lock` | Freezes a forecast before the deadline |
| `POST /api/v1/admin/rounds/{id}/settle` | Scores the round; takes the official Golden Goal minute |

- **Idempotent forecasts.** `generate` saves a new run only when a pick, the Golden Goal
  minute or a probability changed materially. Otherwise it returns the existing run with
  `created: false`. A locked round is never re-forecast, and nothing is forecast after the
  deadline.
- **Audit log.** Every admin change is appended to `reports/admin_audit.jsonl` (action,
  round, run, time). It holds no tokens and no personal data.
- **Wrong tokens are throttled.** After 10 wrong tokens in five minutes, all admin calls
  are refused until the window passes.
- **Nothing is submitted to Super 6.** Entries are always made by hand on the official
  site.

## Running it safely

- `super6 serve` listens on `127.0.0.1` only. To reach it from other machines, put a
  reverse proxy in front that adds HTTPS and rate limiting. Don't expose it directly.
- Browsers on other origins are refused unless listed in `SUPER6_CORS_ORIGINS`.
- The read endpoints are public by design: they hold forecasts for a free-to-play game and
  no personal data.

## Response times

Measured on the development database (about 30,000 matches): every read endpoint answered
in under 0.11 seconds, and most in under 0.03. `generate` fits the model, so it takes a few
seconds.
