"""Restricted endpoints: import and confirm a round, forecast it, lock it, settle it.

Every route needs ``Authorization: Bearer <SUPER6_ADMIN_TOKEN>`` and is written to the
audit log. None of them submits anything to Super 6: entries are always made by hand.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from fastapi import Path as PathParam
from sqlalchemy import select
from sqlalchemy.orm import Session

from super6.api import queries
from super6.api import schemas as s
from super6.api.deps import Admin, ApiContext, Context, Db
from super6.ingestion.canonicalize import TeamRegistry
from super6.ingestion.round_import import (
    RoundImportError,
    import_round,
    next_saturday,
    parse_fixture_text,
)
from super6.models.base import CutoffAfterKickoffError
from super6.prediction.round_spec import (
    ResolvedRound,
    confirm_round_file,
    load_round,
    resolve_round,
)
from super6.prediction.service import (
    IllustrativeRoundError,
    LockError,
    RoundConflictError,
    equivalent_saved_run,
    forecast_round,
    lock_run,
    persist_forecast,
    settle_round,
    upsert_round,
)
from super6.storage.models import FROZEN_STATUSES, PredictionRun
from super6.storage.repositories import load_registry
from super6.timeutil import UK

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Admin])

RoundId = Annotated[str, PathParam(pattern=s.ROUND_ID_PATTERN)]
UNPROCESSABLE = 422  # the named constant changed between Starlette versions
RunIdPath = Annotated[str, PathParam(pattern=r"^run-[0-9a-f]{16}$")]


def _round_file(ctx: ApiContext, round_id: str) -> Path:
    path = ctx.rounds_dir / f"{round_id}.yaml"
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no round file for {round_id}")
    return path


def _frozen_run(session: Session, round_id: str) -> PredictionRun | None:
    return session.scalar(select(PredictionRun).where(
        PredictionRun.round_id == round_id, PredictionRun.status.in_(FROZEN_STATUSES)))


def _store_round(session: Session, resolved: ResolvedRound) -> None:
    try:
        upsert_round(session, resolved)
    except RoundConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/rounds/import", response_model=s.ImportRoundResponse)
def import_round_endpoint(body: s.ImportRoundRequest, ctx: Context, session: Db
                          ) -> s.ImportRoundResponse:
    """Build a round from the six matchups shown in the Super 6 app.

    Competitions and kick-off times come from the provider's fixture list; the deadline is
    the first kick-off. Set ``confirmed`` only if the six were checked against the app.
    """
    now = ctx.now()
    registry = load_registry(session, TeamRegistry.from_config())
    day = body.round_date or next_saturday(now.astimezone(UK).date())
    try:
        imported = import_round(parse_fixture_text(body.text), registry,
                                ctx.load_history(session), day,
                                round_number=body.round_number,
                                known_competitions=ctx.known_competitions)
    except RoundImportError as exc:
        raise HTTPException(UNPROCESSABLE,
                            {"message": "round not imported", "problems": exc.problems}
                            ) from exc
    if _frozen_run(session, imported.round_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"round {imported.round_id} already has a locked forecast")
    confirmation = (now, body.confirmed_source or "Checked against the Super 6 app at import"
                    ) if body.confirmed else None
    ctx.rounds_dir.mkdir(parents=True, exist_ok=True)
    path = ctx.rounds_dir / f"{imported.round_id}.yaml"
    path.write_text(imported.to_yaml(ctx.rules.rules_version,
                                     f"Imported through the API on {now:%Y-%m-%d %H:%M} UTC",
                                     confirmation), encoding="utf-8")
    resolved = resolve_round(load_round(path), registry, ctx.rules, ctx.known_competitions)
    if resolved.fixtures and not resolved.block_reasons:
        _store_round(session, resolved)
    ctx.audit("rounds.import", round_id=imported.round_id, confirmed=body.confirmed,
              fixtures=len(imported.fixtures), block_reasons=resolved.block_reasons)
    return s.ImportRoundResponse(
        round_id=imported.round_id, deadline_at=imported.deadline_utc,
        confirmed=body.confirmed,
        fixtures=[s.ImportedFixtureOut(
            home_team=registry.name(f.home_team), away_team=registry.name(f.away_team),
            competition=f.competition, kickoff_utc=f.kickoff_utc, kickoff_source=f.source)
            for f in imported.fixtures],
        warnings=[*imported.warnings, *resolved.warnings],
        block_reasons=resolved.block_reasons)


@router.post("/rounds/{round_id}/confirm", response_model=s.RoundResponse)
def confirm_round(round_id: RoundId, body: s.ConfirmRoundRequest, ctx: Context, session: Db
                  ) -> s.RoundResponse:
    """Record that a person checked the round's six fixtures against the Super 6 app."""
    path = _round_file(ctx, round_id)
    if _frozen_run(session, round_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"round {round_id} is already locked")
    now = ctx.now()
    spec = confirm_round_file(path, body.source, now)
    registry = load_registry(session, TeamRegistry.from_config())
    resolved = resolve_round(spec, registry, ctx.rules, ctx.known_competitions)
    if resolved.block_reasons:
        raise HTTPException(UNPROCESSABLE,
                            {"message": "round is not valid", "problems": resolved.block_reasons})
    _store_round(session, resolved)
    ctx.audit("rounds.confirm", round_id=round_id, source=body.source)
    return queries.round_detail(session, queries.get_round(session, round_id), now)


@router.post("/predictions/generate", response_model=s.GenerateResponse)
def generate_predictions(body: s.GenerateRequest, ctx: Context, session: Db
                         ) -> s.GenerateResponse:
    """Forecast a round from its round file. Idempotent.

    A new run is saved only when a pick, the Golden Goal minute or a probability changed
    materially. Otherwise the existing run is returned. A locked round is never re-forecast.
    """
    spec = load_round(_round_file(ctx, body.round_id))
    now = ctx.now()
    if now >= spec.deadline_utc:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"the deadline for {spec.round_id} has passed")
    with ctx.generate_lock:
        locked = _frozen_run(session, spec.round_id)
        if locked is not None:
            return s.GenerateResponse(
                round_id=spec.round_id, run_id=locked.id, status=locked.status, created=False,
                reason="already locked", warnings=[str(w) for w in locked.warnings],
                block_reasons=[str(b) for b in locked.block_reasons])
        notes: list[str] = []
        if body.refresh_data:
            if ctx.refresh is None:
                notes.append("REFRESH_UNAVAILABLE: data refresh is not configured here")
            else:
                try:
                    ctx.refresh(session)
                except Exception as exc:  # stale data is reported, not fatal
                    notes.append(f"REFRESH_FAILED: {type(exc).__name__}: {exc}")
        registry = load_registry(session, TeamRegistry.from_config())
        resolved = resolve_round(spec, registry, ctx.rules, ctx.known_competitions)
        if not resolved.fixtures:
            raise HTTPException(UNPROCESSABLE,
                                {"message": "no fixtures could be resolved",
                                 "problems": resolved.block_reasons})
        cutoff = min(now, spec.deadline_utc - timedelta(minutes=1))
        try:
            fc = forecast_round(ctx.load_history(session), resolved, cutoff,
                                ctx.model_factory(), ctx.rules,
                                ctx.model_defaults.get("golden_goal"))
        except CutoffAfterKickoffError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        same = equivalent_saved_run(session, spec.round_id, fc, spec.model_dump(mode="json"))
        if same is not None:
            return s.GenerateResponse(
                round_id=spec.round_id, run_id=same.id, status=same.status, created=False,
                reason="forecast unchanged", warnings=[*notes, *map(str, same.warnings)],
                block_reasons=[str(b) for b in same.block_reasons])
        try:
            run = persist_forecast(session, resolved, fc, publish=body.publish)
        except IllustrativeRoundError as exc:
            raise HTTPException(UNPROCESSABLE, str(exc)) from exc
        except RoundConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        ctx.audit("predictions.generate", round_id=spec.round_id, run_id=run.id,
                  status=run.status)
        return s.GenerateResponse(
            round_id=spec.round_id, run_id=run.id, status=run.status, created=True,
            reason="new forecast", warnings=[*notes, *fc.warnings],
            block_reasons=fc.block_reasons)


@router.post("/runs/{run_id}/lock", response_model=s.LockResponse)
def lock(run_id: RunIdPath, ctx: Context, session: Db) -> s.LockResponse:
    """Freeze a forecast before the deadline. A locked run can never be edited or deleted."""
    if session.get(PredictionRun, run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no run {run_id}")
    try:
        run = lock_run(session, run_id, ctx.now())
    except LockError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    assert run.locked_at is not None
    ctx.audit("runs.lock", round_id=run.round_id, run_id=run.id)
    return s.LockResponse(run_id=run.id, round_id=run.round_id, status=run.status,
                          locked_at=run.locked_at)


@router.post("/rounds/{round_id}/settle", response_model=s.SettleResponse)
def settle(round_id: RoundId, body: s.SettleRequest, ctx: Context, session: Db
           ) -> s.SettleResponse:
    """Score the round's forecasts against ingested results.

    ``golden_goal_minute`` is the official answer from the app. Without it, the minute is
    worked out from stored goal minutes when every fixture is covered.
    """
    queries.get_round(session, round_id)
    results = settle_round(session, round_id, ctx.rules, body.allow_missing,
                           body.golden_goal_minute)
    ctx.audit("rounds.settle", round_id=round_id, golden_goal_minute=body.golden_goal_minute,
              runs=len(results))
    return s.SettleResponse(round_id=round_id, settlements=[
        s.SettlementOut(run_id=r.run_id, status=r.status, settled_fixtures=r.settled_fixtures,
                        missing_fixtures=r.missing_fixtures, metrics=r.metrics)
        for r in results])
