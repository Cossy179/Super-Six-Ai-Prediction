"""Read-only endpoints (DESIGN.md §15). They never fit a model or change a forecast."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError

from super6.api import queries
from super6.api import schemas as s
from super6.api.deps import Context, Db
from super6.config import code_version
from super6.storage.db import session_scope

router = APIRouter(prefix="/api/v1", tags=["public"])

RoundId = Annotated[str, Path(pattern=s.ROUND_ID_PATTERN)]
FixtureId = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")]
RunId = Annotated[str | None, Query(pattern=r"^run-[0-9a-f]{16}$",
                                    description="Default: the round's official run")]


@router.get("/health", response_model=s.HealthResponse)
def health(ctx: Context, response: Response) -> s.HealthResponse:
    """Service, data and scheduler status. 503 when the database cannot be reached."""
    now = ctx.now()
    defaults = ctx.model_defaults
    try:
        with session_scope(ctx.engine) as session:
            return queries.health(session, rules=ctx.rules, defaults=defaults,
                                  reports_dir=ctx.reports_dir,
                                  admin_enabled=ctx.admin_enabled, now=now)
    except SQLAlchemyError as exc:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return s.HealthResponse(
            status="error", generated_at=now, code_version=code_version(),
            rules_version=ctx.rules.rules_version, rules_verified=ctx.rules.verified,
            default_model=str(defaults.get("default_model", "")),
            golden_goal_profile=str(defaults.get("golden_goal", {}).get("profile", "uniform")),
            admin_enabled=ctx.admin_enabled,
            checks=[s.Check(name="database", status="error",
                            detail=f"unreachable: {type(exc).__name__}")],
            latest_result_at=None, sources=[], odds=[], goal_events=0, quality_issues={},
            last_cycle_at=None, recent_alerts=[], attributions=[])


@router.get("/rounds", response_model=list[s.RoundListItem])
def list_rounds(ctx: Context, session: Db,
                limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[s.RoundListItem]:
    """Stored rounds, newest first: the archive of past forecasts."""
    return queries.list_rounds(session, ctx.now(), limit)


@router.get("/rounds/current", response_model=s.RoundResponse)
def current_round(ctx: Context, session: Db) -> s.RoundResponse:
    """The next round by deadline, or the most recent one if none is upcoming."""
    now = ctx.now()
    return queries.round_detail(session, queries.current_round(session, now), now)


@router.get("/rounds/{round_id}", response_model=s.RoundResponse)
def get_round(round_id: RoundId, ctx: Context, session: Db) -> s.RoundResponse:
    return queries.round_detail(session, queries.get_round(session, round_id), ctx.now())


@router.get("/rounds/{round_id}/predictions", response_model=s.PredictionsResponse)
def round_predictions(round_id: RoundId, ctx: Context, session: Db,
                      run_id: RunId = None) -> s.PredictionsResponse:
    """Both optimisation modes and the Golden Goal. Check ``is_locked`` before relying on it."""
    queries.get_round(session, round_id)
    return queries.predictions(session, queries.get_run(session, round_id, run_id), ctx.rules)


@router.get("/fixtures/{fixture_id}/distribution", response_model=s.DistributionResponse)
def fixture_distribution(fixture_id: FixtureId, session: Db,
                         run_id: RunId = None) -> s.DistributionResponse:
    """The full score grid and home/draw/away probabilities."""
    run = queries.run_for_fixture(session, fixture_id, run_id)
    return queries.distribution(session, run, fixture_id)


@router.get("/fixtures/{fixture_id}/explanation", response_model=s.ExplanationResponse)
def fixture_explanation(fixture_id: FixtureId, ctx: Context, session: Db,
                        run_id: RunId = None) -> s.ExplanationResponse:
    """What the forecast used: odds, model, recent results, and why each pick was made."""
    run = queries.run_for_fixture(session, fixture_id, run_id)
    return queries.explanation(session, run, fixture_id, ctx.rules)


@router.get("/performance", response_model=s.PerformanceResponse)
def performance(ctx: Context, session: Db) -> s.PerformanceResponse:
    """Live settled rounds and logged backtests, kept separate and labelled."""
    return queries.performance(session, ctx.reports_dir,
                               str(ctx.model_defaults.get("default_model", "")))


@router.get("/models", response_model=s.ModelsResponse)
def models(ctx: Context, session: Db) -> s.ModelsResponse:
    """The default model, its components, the challengers and the baselines."""
    return queries.models_summary(session, ctx.model_defaults)
