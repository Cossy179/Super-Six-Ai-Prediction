"""Round prediction workflow (DESIGN.md §4.1, §11, §14.2).

``forecast_round`` is pure: history + fixtures + cutoff + model → forecast. It makes no
network calls. ``persist_forecast`` writes the run and its full audit trail;
``lock_run`` and ``settle_round`` implement the rest of the lifecycle.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from super6.config import code_version
from super6.domain import FixtureSpec, FixtureStatus, MarketOdds
from super6.evaluation.metrics import actual_result, exact_log_loss, ranked_probability_score
from super6.features.point_in_time import MatchHistory, empty_odds_frame
from super6.golden_goal.simulate import GoldenGoalFixture, GoldenGoalForecast, simulate_golden_goal
from super6.ingestion.canonicalize import TeamRegistry
from super6.models.base import Prediction, ScoreModel
from super6.prediction.optimiser import (
    TIE_BREAK_RULE,
    Objective,
    RoundPicks,
    choose_round,
    super6_points,
)
from super6.prediction.round_spec import ResolvedRound
from super6.prediction.score_grid import ScoreDistribution
from super6.rules import CompetitionRules
from super6.storage.models import (
    Evaluation,
    Fixture,
    FixtureForecast,
    FixtureResult,
    GoldenGoalPrediction,
    PredictionRun,
    Recommendation,
    RunStatus,
    ScoreProbability,
    Super6Round,
    Super6RoundFixture,
)
from super6.storage.repositories import (
    register_feature_snapshot,
    register_model,
    stable_hash,
    transition_run,
)
from super6.timeutil import iso

STALE_RESULTS_DAYS = 10
STALE_TEAM_DAYS = 28


@dataclass
class RoundForecast:
    round_id: str
    cutoff: pd.Timestamp
    deadline: datetime
    model_name: str
    model_summary: dict[str, Any]
    model_config: dict[str, Any]
    training_fingerprint: str
    n_training_matches: int
    fixtures: list[FixtureSpec]
    predictions: list[Prediction]
    picks: dict[Objective, RoundPicks]
    golden_goal: GoldenGoalForecast | None
    rules: CompetitionRules
    code_version: str
    input_fingerprint: str
    warnings: list[str] = field(default_factory=list)
    block_reasons: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def run_id(self) -> str:
        return f"run-{self.input_fingerprint[:16]}"

    @property
    def status(self) -> RunStatus:
        return RunStatus.BLOCKED if self.block_reasons else RunStatus.VALIDATED


def _odds_frame(odds: Mapping[str, MarketOdds]) -> pd.DataFrame:
    if not odds:
        return empty_odds_frame()
    return pd.DataFrame([
        {"fixture_id": fid, "odds_type": o.odds_type, "source": o.source,
         "available_at": o.available_at, "home": o.home, "draw": o.draw, "away": o.away,
         "over25": o.over25, "under25": o.under25}
        for fid, o in odds.items()
    ])


def _staleness_warnings(history_view_results: pd.DataFrame, cutoff: pd.Timestamp,
                        fixtures: list[FixtureSpec]) -> list[str]:
    warnings: list[str] = []
    if history_view_results.empty:
        return ["NO_RESULTS_IN_VIEW"]
    latest = history_view_results["result_available_at"].max()
    gap = (cutoff - latest).days
    if gap > STALE_RESULTS_DAYS:
        warnings.append(f"RESULTS_STALE: newest result is {gap} days before the cutoff; "
                        "refresh data (super6 data download && super6 data ingest)")
    for fx in fixtures:
        for team in (fx.home_team, fx.away_team):
            played = history_view_results.loc[
                (history_view_results["home_team"] == team)
                | (history_view_results["away_team"] == team), "kickoff_utc"]
            if played.empty:
                warnings.append(f"TEAM_HAS_NO_HISTORY:{team}")
            elif (cutoff - played.max()).days > STALE_TEAM_DAYS:
                warnings.append(f"TEAM_DATA_STALE:{team}:{(cutoff - played.max()).days}d")
    return warnings


def forecast_round(
    history: MatchHistory,
    resolved: ResolvedRound,
    cutoff: datetime | pd.Timestamp,
    model: ScoreModel,
    rules: CompetitionRules,
    golden_goal_config: Mapping[str, Any] | None = None,
) -> RoundForecast:
    cutoff_ts = pd.Timestamp(cutoff).tz_convert(UTC)
    warnings = [*rules.warnings(), *resolved.warnings]
    blocks = list(resolved.block_reasons)
    deadline = resolved.spec.deadline_utc
    if cutoff_ts > pd.Timestamp(deadline):
        blocks.append(f"CUTOFF_AFTER_DEADLINE: {cutoff_ts} > {deadline}")
    hist = history.with_extra(odds=_odds_frame(resolved.odds)) if resolved.odds else history
    view = hist.view(cutoff_ts)
    fitted = model.fit(view)
    predictions: list[Prediction] = []
    for fx in resolved.fixtures:
        pred = fitted.predict(fx, view)
        problems = [p for p in pred.distribution.invariant_violations(model.grid.tail_tolerance)
                    if not p.startswith("TAIL_ABOVE_TOLERANCE")]
        if problems:
            blocks.append(f"PROBABILITY_INTEGRITY:{fx.fixture_id}:{','.join(problems)}")
        predictions.append(pred)
        warnings.extend(f"{fx.fixture_id}:{w}" for w in pred.warnings)
    warnings.extend(_staleness_warnings(view.results, cutoff_ts, resolved.fixtures))
    dists: dict[str, ScoreDistribution] = {p.fixture_id: p.distribution for p in predictions}
    picks = {obj: choose_round(dists, obj, rules) for obj in Objective}
    gg: GoldenGoalForecast | None = None
    gg_cfg = dict(golden_goal_config or {})
    if rules.golden_goal_enabled and predictions:
        gg = simulate_golden_goal(
            [GoldenGoalFixture(fx.fixture_id, fx.kickoff_utc, p.lam_home + p.lam_away)
             for fx, p in zip(resolved.fixtures, predictions, strict=True)],
            rules,
            n_simulations=int(gg_cfg.get("n_simulations", 50_000)),
            seed=int(gg_cfg.get("seed", 20261105)),
            first_half_stoppage_minutes=float(gg_cfg.get("first_half_stoppage_minutes", 2.0)),
            second_half_stoppage_minutes=float(gg_cfg.get("second_half_stoppage_minutes", 5.0)),
            half_time_break_minutes=float(gg_cfg.get("half_time_break_minutes", 15.0)),
        )
        warnings.append("GOLDEN_GOAL_BASELINE_ONLY")
    version = code_version()
    fingerprint = stable_hash({
        "view": view.fingerprint(),
        "round": resolved.spec.model_dump(mode="json"),
        "fixtures": [f.fixture_id for f in resolved.fixtures],
        "model": model.name,
        "model_config": model.config,
        "grid": model.grid.__dict__,
        "rules": rules.model_dump(mode="json"),
        "golden_goal": gg_cfg,
        "cutoff": cutoff_ts.isoformat(),
        "code_version": version,
    })
    return RoundForecast(
        round_id=resolved.spec.round_id, cutoff=cutoff_ts, deadline=deadline,
        model_name=model.name, model_summary=fitted.describe(), model_config=dict(model.config),
        training_fingerprint=fitted.training_fingerprint,
        n_training_matches=fitted.n_training_matches, fixtures=list(resolved.fixtures),
        predictions=predictions, picks=picks, golden_goal=gg, rules=rules,
        code_version=version, input_fingerprint=fingerprint, warnings=warnings,
        block_reasons=blocks,
    )


# --------------------------------------------------------------------------- output
def _alternatives(picks: RoundPicks, fixture_id: str) -> list[dict[str, Any]]:
    fp = next(f for f in picks.fixtures if f.fixture_id == fixture_id)
    return [{"home_goals": a.home_goals, "away_goals": a.away_goals,
             "probability": round(a.exact_probability, 6),
             "expected_points": round(a.expected_points, 6)} for a in fp.alternatives]


def forecast_to_json(fc: RoundForecast, registry: TeamRegistry | None = None,
                     status: str | None = None) -> dict[str, Any]:
    """API-shaped payload (DESIGN.md §15.1) carrying both optimisation modes."""
    name = registry.name if registry else (lambda t: t)
    fixtures = []
    for fx, pred in zip(fc.fixtures, fc.predictions, strict=True):
        out = pred.distribution.outcome_probabilities()
        fixtures.append({
            "fixture_id": fx.fixture_id,
            "competition": fx.competition,
            "home_team": name(fx.home_team),
            "away_team": name(fx.away_team),
            "kickoff_utc": iso(fx.kickoff_utc),
            "expected_goals": {"home": round(pred.lam_home, 4), "away": round(pred.lam_away, 4)},
            "outcome_probabilities": {k: round(v, 6) for k, v in out.as_dict().items()},
            "tail_probability": pred.distribution.tail,
            "top_scores": [{"home_goals": s.home_goals, "away_goals": s.away_goals,
                            "probability": round(s.probability, 6)}
                           for s in pred.distribution.top_scores(5)],
            "warnings": list(pred.warnings),
        })
    modes: dict[str, Any] = {}
    for obj, picks in fc.picks.items():
        modes[obj.value] = {
            "expected_points": round(picks.expected_points, 4),
            "joint_exact_probability": picks.joint_exact_probability,
            "joint_probability_assumptions": list(picks.assumptions),
            "fixtures": [
                {"fixture_id": f.fixture_id,
                 "predicted_home_goals": f.pick.home_goals,
                 "predicted_away_goals": f.pick.away_goals,
                 "exact_score_probability": round(f.pick.exact_probability, 6),
                 "correct_result_probability": round(f.pick.result_probability, 6),
                 "expected_points": round(f.pick.expected_points, 6),
                 "top_alternatives": _alternatives(picks, f.fixture_id)}
                for f in picks.fixtures
            ],
        }
    gg = None
    if fc.golden_goal is not None:
        g = fc.golden_goal
        gg = {"minute": g.minute, "method": g.method,
              "expected_absolute_error_minutes": round(g.expected_abs_error, 2),
              "interval_80": [g.interval_low, g.interval_high],
              "p_no_goal": g.p_no_goal, "assumptions": list(g.assumptions)}
    return {
        "round_id": fc.round_id,
        "prediction_run_id": fc.run_id,
        "status": status or fc.status.value,
        "generated_at": iso(fc.generated_at),
        "cutoff_at": iso(fc.cutoff),
        "deadline_at": iso(fc.deadline),
        "model_version": f"{fc.model_name}@{fc.code_version}",
        "rules_version": fc.rules.rules_version,
        "default_mode": Objective.POINTS.value,
        "tie_break_rule": TIE_BREAK_RULE,
        "training": {"matches": fc.n_training_matches, "fingerprint": fc.training_fingerprint},
        "fixtures": fixtures,
        "modes": modes,
        "golden_goal": gg,
        "warnings": fc.warnings,
        "block_reasons": fc.block_reasons,
    }


# --------------------------------------------------------------------------- persistence
class RoundConflictError(RuntimeError):
    pass


def _upsert_round(session: Session, resolved: ResolvedRound) -> Super6Round:
    spec = resolved.spec
    spec_json = spec.model_dump(mode="json")
    rnd = session.get(Super6Round, spec.round_id)
    if rnd is not None and rnd.spec != spec_json:
        frozen = session.scalar(select(PredictionRun.id).where(
            PredictionRun.round_id == rnd.id,
            PredictionRun.status.in_([RunStatus.LOCKED.value, RunStatus.SETTLED.value])))
        if frozen:
            raise RoundConflictError(
                f"round {rnd.id} already has locked run {frozen}; give the changed round a new "
                "round_id rather than editing it")
        rnd.spec = spec_json
        rnd.deadline_utc = spec.deadline_utc
        rnd.rules_version = spec.rules_version
        rnd.illustrative = spec.illustrative
        rnd.source_note = spec.source_note
        for rf in list(rnd.fixtures):
            session.delete(rf)
        session.flush()
    if rnd is None:
        rnd = Super6Round(id=spec.round_id, deadline_utc=spec.deadline_utc,
                          rules_version=spec.rules_version, status="OPEN",
                          illustrative=spec.illustrative, source_note=spec.source_note,
                          spec=spec_json)
        session.add(rnd)
        session.flush()
    existing = {rf.fixture_id for rf in rnd.fixtures}
    for order, fx in enumerate(resolved.fixtures, start=1):
        if session.get(Fixture, fx.fixture_id) is None:
            local = pd.Timestamp(fx.kickoff_utc).tz_convert("Europe/London")
            season = f"{local.year if local.month >= 7 else local.year - 1}"
            season = f"{season}-{(int(season) + 1) % 100:02d}"
            session.add(Fixture(id=fx.fixture_id, competition_id=fx.competition, season=season,
                                home_team_id=fx.home_team, away_team_id=fx.away_team,
                                kickoff_utc=fx.kickoff_utc, kickoff_time_known=True,
                                neutral=fx.neutral, status=FixtureStatus.SCHEDULED.value))
            session.flush()
        if fx.fixture_id not in existing:
            session.add(Super6RoundFixture(round_id=rnd.id, fixture_id=fx.fixture_id,
                                           display_order=order))
    session.flush()
    return rnd


def persist_forecast(session: Session, resolved: ResolvedRound, fc: RoundForecast,
                     publish: bool = False) -> PredictionRun:
    """Write the run (idempotent: identical inputs return the existing run)."""
    existing = session.get(PredictionRun, fc.run_id)
    if existing is not None:
        return existing
    _upsert_round(session, resolved)
    register_feature_snapshot(session, fc.training_fingerprint, fc.cutoff.to_pydatetime(),
                              fc.n_training_matches)
    entry = register_model(session, model_name=fc.model_name, code_version=fc.code_version,
                           config=fc.model_config, training_cutoff=fc.cutoff.to_pydatetime(),
                           training_fingerprint=fc.training_fingerprint,
                           summary=fc.model_summary)
    run = PredictionRun(
        id=fc.run_id, round_id=fc.round_id, model_registry_id=entry.id,
        cutoff_at=fc.cutoff.to_pydatetime(), created_at=fc.generated_at,
        status=RunStatus.DRAFT.value, code_version=fc.code_version,
        rules_version=fc.rules.rules_version, input_fingerprint=fc.input_fingerprint,
        config=json.loads(json.dumps({"model": fc.model_config, "tie_break": TIE_BREAK_RULE},
                                     default=str)),
        warnings=fc.warnings, block_reasons=fc.block_reasons,
        published_at=None, locked_at=None, settled_at=None,
    )
    session.add(run)
    session.flush()
    for order, (fx, pred) in enumerate(zip(fc.fixtures, fc.predictions, strict=True), start=1):
        dist = pred.distribution
        out = dist.outcome_probabilities()
        session.add(FixtureForecast(
            run_id=run.id, fixture_id=fx.fixture_id, display_order=order,
            lam_home=pred.lam_home, lam_away=pred.lam_away, p_home=out.home, p_draw=out.draw,
            p_away=out.away, tail_probability=dist.tail, grid_max_goals=dist.max_goals,
            warnings=list(pred.warnings),
            details=json.loads(json.dumps(dict(pred.details), default=str))))
        session.add_all([
            ScoreProbability(run_id=run.id, fixture_id=fx.fixture_id, home_goals=r["home_goals"],
                             away_goals=r["away_goals"], probability=r["probability"])
            for r in dist.to_records()
        ])
    for obj, picks in fc.picks.items():
        for f in picks.fixtures:
            session.add(Recommendation(
                run_id=run.id, mode=obj.value, fixture_id=f.fixture_id,
                home_goals=f.pick.home_goals, away_goals=f.pick.away_goals,
                exact_probability=f.pick.exact_probability,
                result_probability=f.pick.result_probability,
                expected_points=f.pick.expected_points,
                alternatives=_alternatives(picks, f.fixture_id)))
    if fc.golden_goal is not None:
        g = fc.golden_goal
        session.add(GoldenGoalPrediction(
            run_id=run.id, minute=g.minute, method=g.method,
            expected_abs_error=g.expected_abs_error, interval_low=g.interval_low,
            interval_high=g.interval_high, p_no_goal=g.p_no_goal,
            details={"distribution": {str(k): v for k, v in g.distribution.items()},
                     "assumptions": list(g.assumptions), "n_simulations": g.n_simulations,
                     "seed": g.seed, "first_goal_fixture_share": g.first_goal_fixture_share}))
    session.flush()
    transition_run(session, run, fc.status)
    if publish and fc.status is RunStatus.VALIDATED:
        transition_run(session, run, RunStatus.PUBLISHED)
    return run


class LockError(RuntimeError):
    pass


def lock_run(session: Session, run_id: str, now: datetime | None = None) -> PredictionRun:
    now = now or datetime.now(UTC)
    run = session.get(PredictionRun, run_id)
    if run is None:
        raise LockError(f"no run {run_id}")
    rnd = session.get(Super6Round, run.round_id)
    assert rnd is not None
    if now > rnd.deadline_utc:
        raise LockError(f"deadline {iso(rnd.deadline_utc)} has passed; a locked record must be "
                        "made before the deadline")
    other = session.scalar(select(PredictionRun.id).where(
        PredictionRun.round_id == run.round_id, PredictionRun.id != run.id,
        PredictionRun.status.in_([RunStatus.LOCKED.value, RunStatus.SETTLED.value])))
    if other:
        raise LockError(f"round {run.round_id} already has locked run {other}")
    if run.status not in (RunStatus.VALIDATED.value, RunStatus.PUBLISHED.value):
        raise LockError(f"only VALIDATED or PUBLISHED runs can be locked (run is {run.status})")
    transition_run(session, run, RunStatus.LOCKED, now)
    return run


@dataclass
class Settlement:
    run_id: str
    status: str
    settled_fixtures: int
    missing_fixtures: list[str]
    metrics: dict[str, dict[str, float]]


def settle_round(session: Session, round_id: str, rules: CompetitionRules,
                 allow_missing: bool = False) -> list[Settlement]:
    """Score every non-blocked run of a round; move its locked run to SETTLED."""
    rnd = session.get(Super6Round, round_id)
    if rnd is None:
        raise KeyError(f"no round {round_id}")
    fixture_ids = [rf.fixture_id for rf in rnd.fixtures]
    results = {r.fixture_id: (r.home_goals, r.away_goals)
               for r in session.scalars(select(FixtureResult).where(
                   FixtureResult.fixture_id.in_(fixture_ids)).order_by(FixtureResult.recorded_at))
               if r.fixture_id}
    missing = [f for f in fixture_ids if f not in results]
    out: list[Settlement] = []
    runs = session.scalars(select(PredictionRun).where(
        PredictionRun.round_id == round_id,
        PredictionRun.status != RunStatus.BLOCKED.value)).all()
    for run in runs:
        if missing and not allow_missing:
            out.append(Settlement(run.id, run.status, len(fixture_ids) - len(missing), missing,
                                  {}))
            continue
        metrics: dict[str, dict[str, float]] = {}
        for mode in Objective:
            recs = session.scalars(select(Recommendation).where(
                Recommendation.run_id == run.id, Recommendation.mode == mode.value)).all()
            pts = [super6_points(r.home_goals, r.away_goals, *results[r.fixture_id], rules)
                   for r in recs if r.fixture_id in results]
            exact = [(r.home_goals, r.away_goals) == results[r.fixture_id]
                     for r in recs if r.fixture_id in results]
            metrics[mode.value] = {"points": float(sum(pts)), "exact_hits": float(sum(exact)),
                                   "fixtures_settled": float(len(pts))}
        ll, rps = [], []
        for fid, (hg, ag) in results.items():
            cells = session.scalars(select(ScoreProbability).where(
                ScoreProbability.run_id == run.id, ScoreProbability.fixture_id == fid)).all()
            ff = session.get(FixtureForecast, (run.id, fid))
            if not cells or ff is None:
                continue
            side = ff.grid_max_goals + 1
            grid = np.zeros((side, side))
            for c in cells:
                grid[c.home_goals, c.away_goals] = c.probability
            dist = ScoreDistribution(grid, ff.tail_probability)
            ll.append(exact_log_loss(dist, hg, ag))
            rps.append(ranked_probability_score(dist.outcome_probabilities(),
                                                actual_result(hg, ag)))
        metrics["forecast"] = {"exact_log_loss_mean": float(sum(ll) / len(ll)) if ll else 0.0,
                               "rps_mean": float(sum(rps) / len(rps)) if rps else 0.0,
                               "void_fixtures": float(len(missing))}
        have = {(e.mode, e.metric) for e in session.scalars(
            select(Evaluation).where(Evaluation.run_id == run.id))}
        for group, vals in metrics.items():
            for metric, value in vals.items():
                if (group, metric) not in have:
                    session.add(Evaluation(run_id=run.id, mode=group, metric=metric,
                                           value=value))
        if run.status == RunStatus.LOCKED.value:
            transition_run(session, run, RunStatus.SETTLED)
        out.append(Settlement(run.id, run.status, len(fixture_ids) - len(missing), missing,
                              metrics))
    if not missing:
        rnd.status = "SETTLED"
    elif allow_missing:
        rnd.status = "PARTIALLY_SETTLED"
    session.flush()
    return out
