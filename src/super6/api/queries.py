"""Read side of the API: responses built from what the prediction service stored.

Nothing here fits a model or changes a forecast. A response for a run is rebuilt from the
run's stored rows, so a locked run always reads back exactly as it was locked.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from super6.api import schemas as s
from super6.config import code_version, load_yaml
from super6.domain import MarketOdds
from super6.evaluation.tracking import read_log
from super6.models import MODEL_CLASSES
from super6.models.market import devig
from super6.prediction.optimiser import JOINT_ASSUMPTIONS, Objective, super6_points
from super6.prediction.service import STALE_RESULTS_DAYS, basis_from
from super6.rules import CompetitionRules
from super6.storage.models import (
    FROZEN_STATUSES,
    DataQualityIssue,
    Evaluation,
    Fixture,
    FixtureForecast,
    FixtureResult,
    GoalEvent,
    GoldenGoalPrediction,
    MarketSnapshot,
    ModelRegistryEntry,
    PredictionRun,
    Recommendation,
    RunStatus,
    ScoreProbability,
    SourceSnapshot,
    Super6Round,
    Team,
)
from super6.timeutil import ensure_utc

IN_PLAY_HOURS = 2.5
CYCLE_STALE_HOURS = 3.0
RUN_PREFERENCE = (RunStatus.SETTLED, RunStatus.LOCKED, RunStatus.PUBLISHED, RunStatus.VALIDATED)

# What each warning code means, for people. Unknown codes are shown as they are.
WARNING_MEANINGS: dict[str, str] = {
    "ENSEMBLE_TEAM_MODEL_ONLY": "No bookmaker odds were available before the cutoff, so this "
                                "match uses the results-based model, which backtests worse.",
    "MARKET_ODDS_STALE": "The bookmaker odds used are more than four days old.",
    "UNKNOWN_TEAM": "A team has no match history here; its forecast is an average team's.",
    "LOW_DATA_TEAM": "A team has little match history; its forecast is pulled towards average.",
    "TEAM_HAS_NO_HISTORY": "A team has no results in the data.",
    "TEAM_DATA_STALE": "A team's latest result in the data is old; refresh the data.",
    "RESULTS_STALE": "The newest result in the data is old; refresh the data.",
    "NO_RESULTS_IN_VIEW": "No results were available at the cutoff.",
    "COMPETITION_POOLED": "The competition has too little history; league averages were pooled.",
    "GOLDEN_GOAL_BASELINE_ONLY": "The Golden Goal minute assumes goals are equally likely in "
                                 "every minute (the tested default).",
    "GOLDEN_GOAL_PROFILE_FALLBACK": "Too few goal times were known for the empirical Golden "
                                    "Goal profile, so the uniform one was used.",
    "GOLDEN_GOAL_STAGGERED_KICKOFFS": "Kick-off times differ. The minute shown assumes the "
                                      "lowest goal minute in any game counts; under the "
                                      "first-by-the-clock reading it would be different.",
    "ROUND_UNCONFIRMED": "Nobody has recorded checking these six fixtures against the Super "
                         "6 app, so the forecast cannot be locked.",
    "RULES_UNVERIFIED": "The competition rules file has not been checked against the "
                        "official terms.",
    "ILLUSTRATIVE_DATA_ONLY": "This round is an invented example, not a real one.",
    "EXPLICIT_TAIL_ABOVE_TOLERANCE": "This baseline keeps a share of probability for scores "
                                     "beyond its grid.",
    "DEADLINE_BEFORE_FIRST_KICKOFF": "The round's deadline is earlier than its first kick-off.",
}
_CODE = re.compile(r"[A-Z][A-Z0-9_]{3,}")
_BASELINES = {name for name, cls in MODEL_CLASSES.items() if cls.__module__.endswith("baselines")}


class NotFoundError(LookupError):
    """The requested round, run or fixture is not stored."""


# --------------------------------------------------------------------------- small helpers
def warning_notes(warnings: Sequence[Any]) -> list[s.WarningNote]:
    notes = []
    for raw in warnings:
        text = str(raw)
        code = next((c for c in _CODE.findall(text) if c in WARNING_MEANINGS), None)
        if code is None:
            found = _CODE.search(text)
            code = found.group(0) if found else "NOTE"
        notes.append(s.WarningNote(text=text, code=code,
                                   meaning=WARNING_MEANINGS.get(code, text)))
    return notes


def team_names(session: Session) -> dict[str, str]:
    return {tid: name for tid, name in session.execute(select(Team.id, Team.canonical_name))}


def _results(session: Session, fixture_ids: Sequence[str]) -> dict[str, s.Score]:
    out: dict[str, s.Score] = {}
    for r in session.scalars(select(FixtureResult).where(
            FixtureResult.fixture_id.in_(list(fixture_ids))).order_by(FixtureResult.recorded_at)):
        out.setdefault(r.fixture_id, s.Score(home_goals=r.home_goals, away_goals=r.away_goals))
    return out


def _model_version(session: Session, run: PredictionRun) -> tuple[str, str]:
    entry = session.get(ModelRegistryEntry, run.model_registry_id)
    name = entry.model_name if entry else "unknown"
    return name, f"{name}@{run.code_version}"


def get_round(session: Session, round_id: str) -> Super6Round:
    rnd = session.get(Super6Round, round_id)
    if rnd is None:
        raise NotFoundError(f"no round {round_id}")
    return rnd


def official_run(session: Session, round_id: str) -> PredictionRun | None:
    """The run to show for a round: its locked run, else the latest published or valid one."""
    runs = session.scalars(select(PredictionRun).where(PredictionRun.round_id == round_id)
                           .order_by(PredictionRun.created_at.desc())).all()
    for status in RUN_PREFERENCE:
        for run in runs:
            if run.status == status.value:
                return run
    return runs[0] if runs else None


def get_run(session: Session, round_id: str, run_id: str | None) -> PredictionRun:
    if run_id is None:
        run = official_run(session, round_id)
        if run is None:
            raise NotFoundError(f"round {round_id} has no forecast yet")
        return run
    run = session.get(PredictionRun, run_id)
    if run is None or run.round_id != round_id:
        raise NotFoundError(f"no run {run_id} for round {round_id}")
    return run


def run_for_fixture(session: Session, fixture_id: str, run_id: str | None) -> PredictionRun:
    """The named run, or the official run of the latest round that includes the fixture."""
    if run_id is not None:
        run = session.get(PredictionRun, run_id)
        if run is None or session.get(FixtureForecast, (run_id, fixture_id)) is None:
            raise NotFoundError(f"no forecast for {fixture_id} in run {run_id}")
        return run
    runs = session.scalars(
        select(PredictionRun).join(FixtureForecast, FixtureForecast.run_id == PredictionRun.id)
        .join(Super6Round, Super6Round.id == PredictionRun.round_id)
        .where(FixtureForecast.fixture_id == fixture_id)
        .order_by(Super6Round.deadline_utc.desc())).all()
    if not runs:
        raise NotFoundError(f"no forecast for fixture {fixture_id}")
    official = official_run(session, runs[0].round_id)
    assert official is not None
    return official


# --------------------------------------------------------------------------- rounds
def _phase(rnd: Super6Round, kickoffs: Sequence[datetime], now: datetime) -> s.Phase:
    if rnd.status == "SETTLED":
        return "settled"
    if now < rnd.deadline_utc:
        return "upcoming"
    last = max(kickoffs, default=rnd.deadline_utc)
    return "in_play" if now < last + timedelta(hours=IN_PLAY_HOURS) else "awaiting_results"


def _round_fixtures(session: Session, rnd: Super6Round) -> list[Fixture]:
    out = []
    for link in rnd.fixtures:
        fx = session.get(Fixture, link.fixture_id)
        assert fx is not None
        out.append(fx)
    return out


def round_item(session: Session, rnd: Super6Round, now: datetime) -> s.RoundListItem:
    fixtures = _round_fixtures(session, rnd)
    run = official_run(session, rnd.id)
    return s.RoundListItem(
        round_id=rnd.id, deadline_at=rnd.deadline_utc, status=rnd.status,
        phase=_phase(rnd, [f.kickoff_utc for f in fixtures], now),
        confirmed=bool(rnd.spec.get("confirmed")),
        locked=run is not None and run.status in FROZEN_STATUSES,
        official_run_id=run.id if run else None)


def round_detail(session: Session, rnd: Super6Round, now: datetime) -> s.RoundResponse:
    fixtures = _round_fixtures(session, rnd)
    names = team_names(session)
    results = _results(session, [f.id for f in fixtures])
    runs = session.scalars(select(PredictionRun).where(PredictionRun.round_id == rnd.id)
                           .order_by(PredictionRun.created_at.desc())).all()
    confirmed_at = rnd.spec.get("confirmed_at")
    return s.RoundResponse(
        **round_item(session, rnd, now).model_dump(),
        rules_version=rnd.rules_version,
        confirmed_at=ensure_utc(confirmed_at) if confirmed_at else None,
        confirmed_source=rnd.spec.get("confirmed_source"), source_note=rnd.source_note,
        fixtures=[s.RoundFixture(
            fixture_id=f.id, competition=f.competition_id, home_team_id=f.home_team_id,
            away_team_id=f.away_team_id, home_team=names.get(f.home_team_id, f.home_team_id),
            away_team=names.get(f.away_team_id, f.away_team_id), kickoff_utc=f.kickoff_utc,
            status=f.status, result=results.get(f.id)) for f in fixtures],
        runs=[s.RunSummary(run_id=r.id, status=r.status, created_at=r.created_at,
                           cutoff_at=r.cutoff_at, locked_at=r.locked_at,
                           model_version=_model_version(session, r)[1]) for r in runs])


def current_round(session: Session, now: datetime) -> Super6Round:
    """The next round by deadline; if none is upcoming, the most recent one."""
    upcoming = session.scalars(select(Super6Round).where(Super6Round.deadline_utc >= now)
                               .order_by(Super6Round.deadline_utc)).first()
    rnd = upcoming or session.scalars(select(Super6Round)
                                      .order_by(Super6Round.deadline_utc.desc())).first()
    if rnd is None:
        raise NotFoundError("no rounds are stored yet")
    return rnd


def list_rounds(session: Session, now: datetime, limit: int) -> list[s.RoundListItem]:
    rounds = session.scalars(select(Super6Round).order_by(Super6Round.deadline_utc.desc())
                             .limit(limit)).all()
    return [round_item(session, r, now) for r in rounds]


# --------------------------------------------------------------------------- predictions
def _evaluations(session: Session, run_id: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for e in session.scalars(select(Evaluation).where(Evaluation.run_id == run_id)):
        out[e.mode][e.metric] = e.value
    return out


def _top_scores(cells: Sequence[ScoreProbability], n: int = 5) -> list[s.ScoreProbabilityOut]:
    ranked = sorted(cells, key=lambda c: (-c.probability, c.home_goals + c.away_goals,
                                          c.home_goals))
    return [s.ScoreProbabilityOut(home_goals=c.home_goals, away_goals=c.away_goals,
                                  probability=c.probability) for c in ranked[:n]]


def _pick(rec: Recommendation, result: s.Score | None, rules: CompetitionRules) -> s.Pick:
    points = None if result is None else super6_points(
        rec.home_goals, rec.away_goals, result.home_goals, result.away_goals, rules)
    return s.Pick(
        fixture_id=rec.fixture_id, predicted_home_goals=rec.home_goals,
        predicted_away_goals=rec.away_goals, exact_score_probability=rec.exact_probability,
        correct_result_probability=rec.result_probability, expected_points=rec.expected_points,
        top_alternatives=[s.Alternative(**a) for a in rec.alternatives], points_scored=points)


def _golden_goal(session: Session, run_id: str, evals: Mapping[str, Mapping[str, float]]
                 ) -> s.GoldenGoalOut | None:
    gg = session.get(GoldenGoalPrediction, run_id)
    if gg is None:
        return None
    settled = evals.get("golden_goal", {})
    actual = settled.get("actual_minute")
    source: Literal["official", "goal_minutes"] | None = None
    if actual is not None:
        source = "goal_minutes" if settled.get("from_goal_events") else "official"
    return s.GoldenGoalOut(
        minute=gg.minute, method=gg.method,
        expected_absolute_error_minutes=round(gg.expected_abs_error, 2),
        interval_80=(gg.interval_low, gg.interval_high), p_no_goal=gg.p_no_goal,
        assumptions=list(gg.details.get("assumptions", [])),
        distribution={str(k): float(v) for k, v in gg.details.get("distribution", {}).items()},
        actual_minute=None if actual is None else int(actual),
        absolute_error=None if actual is None else int(abs(gg.minute - actual)),
        actual_source=source)


def predictions(session: Session, run: PredictionRun, rules: CompetitionRules
                ) -> s.PredictionsResponse:
    rnd = get_round(session, run.round_id)
    names = team_names(session)
    model_name, version = _model_version(session, run)
    forecasts = session.scalars(select(FixtureForecast).where(FixtureForecast.run_id == run.id)
                                .order_by(FixtureForecast.display_order)).all()
    order = {f.fixture_id: f.display_order for f in forecasts}
    results = _results(session, list(order))
    cells: dict[str, list[ScoreProbability]] = defaultdict(list)
    for c in session.scalars(select(ScoreProbability).where(ScoreProbability.run_id == run.id)):
        cells[c.fixture_id].append(c)
    fixtures = []
    for ff in forecasts:
        fx = session.get(Fixture, ff.fixture_id)
        assert fx is not None
        fixtures.append(s.FixtureForecastOut(
            fixture_id=fx.id, competition=fx.competition_id,
            home_team=names.get(fx.home_team_id, fx.home_team_id),
            away_team=names.get(fx.away_team_id, fx.away_team_id), kickoff_utc=fx.kickoff_utc,
            expected_goals=s.ExpectedGoals(home=ff.lam_home, away=ff.lam_away),
            outcome_probabilities=s.Outcome(home=ff.p_home, draw=ff.p_draw, away=ff.p_away),
            tail_probability=ff.tail_probability, top_scores=_top_scores(cells[fx.id]),
            forecast_basis=basis_from(model_name, ff.details),
            warnings=[str(w) for w in ff.warnings], result=results.get(fx.id)))
    label = {f.fixture_id: f"{f.home_team} v {f.away_team}" for f in fixtures}
    evals = _evaluations(session, run.id)
    golden_goal = _golden_goal(session, run.id, evals)
    recs: dict[str, list[Recommendation]] = defaultdict(list)
    for rec in session.scalars(select(Recommendation).where(Recommendation.run_id == run.id)):
        recs[rec.mode].append(rec)
    modes: dict[str, s.ModePicks] = {}
    for mode, rows in recs.items():
        rows.sort(key=lambda r: order.get(r.fixture_id, 0))
        picks = [_pick(r, results.get(r.fixture_id), rules) for r in rows]
        joint = 1.0
        for p in picks:
            joint *= p.exact_score_probability
        scored = [p.points_scored for p in picks if p.points_scored is not None]
        lines = [f"{label[p.fixture_id]}: {p.predicted_home_goals}-{p.predicted_away_goals}"
                 for p in picks]
        if golden_goal is not None:
            lines.append(f"Golden Goal: minute {golden_goal.minute}")
        modes[mode] = s.ModePicks(
            expected_points=sum(p.expected_points for p in picks),
            joint_exact_probability=joint,
            joint_probability_assumptions=list(JOINT_ASSUMPTIONS), fixtures=picks,
            points_scored=sum(scored) if scored else None,
            exact_hits=(sum(p == rules.points_exact_score for p in scored) if scored else None),
            entry_summary="\n".join(lines))
    by_mode = {m: {p.fixture_id: (p.predicted_home_goals, p.predicted_away_goals)
                   for p in mp.fixtures} for m, mp in modes.items()}
    points_picks = by_mode.get(Objective.POINTS.value, {})
    jackpot_picks = by_mode.get(Objective.JACKPOT.value, {})
    return s.PredictionsResponse(
        round_id=run.round_id, prediction_run_id=run.id, status=run.status,
        is_locked=run.status in FROZEN_STATUSES, generated_at=run.created_at,
        cutoff_at=run.cutoff_at, deadline_at=rnd.deadline_utc, locked_at=run.locked_at,
        model_version=version, rules_version=run.rules_version,
        default_mode=Objective.POINTS.value, tie_break_rule=str(run.config.get("tie_break", "")),
        fixtures=fixtures, modes=modes,
        differing_fixture_ids=[f for f in order if points_picks.get(f) != jackpot_picks.get(f)],
        golden_goal=golden_goal, warnings=warning_notes(run.warnings),
        block_reasons=[str(b) for b in run.block_reasons])


# --------------------------------------------------------------------------- one fixture
def _fixture_forecast(session: Session, run: PredictionRun, fixture_id: str
                      ) -> tuple[FixtureForecast, Fixture, dict[str, str]]:
    ff = session.get(FixtureForecast, (run.id, fixture_id))
    fx = session.get(Fixture, fixture_id)
    if ff is None or fx is None:
        raise NotFoundError(f"no forecast for {fixture_id} in run {run.id}")
    return ff, fx, team_names(session)


def distribution(session: Session, run: PredictionRun, fixture_id: str
                 ) -> s.DistributionResponse:
    ff, fx, names = _fixture_forecast(session, run, fixture_id)
    cells = session.scalars(select(ScoreProbability).where(
        ScoreProbability.run_id == run.id, ScoreProbability.fixture_id == fixture_id)).all()
    side = ff.grid_max_goals + 1
    grid = [[0.0] * side for _ in range(side)]
    for c in cells:
        grid[c.home_goals][c.away_goals] = c.probability
    return s.DistributionResponse(
        fixture_id=fixture_id, run_id=run.id, round_id=run.round_id,
        home_team=names.get(fx.home_team_id, fx.home_team_id),
        away_team=names.get(fx.away_team_id, fx.away_team_id), kickoff_utc=fx.kickoff_utc,
        forecast_basis=basis_from(_model_version(session, run)[0], ff.details),
        expected_goals=s.ExpectedGoals(home=ff.lam_home, away=ff.lam_away),
        outcome_probabilities=s.Outcome(home=ff.p_home, draw=ff.p_draw, away=ff.p_away),
        tail_probability=ff.tail_probability, max_goals=ff.grid_max_goals, grid=grid,
        home_goals_distribution=[sum(row) for row in grid],
        away_goals_distribution=[sum(grid[h][a] for h in range(side)) for a in range(side)],
        top_scores=_top_scores(cells))


def _form(session: Session, team_id: str, cutoff: datetime, names: Mapping[str, str],
          n: int = 5) -> list[s.FormMatch]:
    """The team's last results that were public at the run's cutoff (newest first)."""
    rows = session.execute(
        select(Fixture, FixtureResult).join(FixtureResult, FixtureResult.fixture_id == Fixture.id)
        .where(or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
               FixtureResult.result_available_at <= cutoff)
        .order_by(Fixture.kickoff_utc.desc()).limit(n)).all()
    out = []
    for fx, res in rows:
        home = fx.home_team_id == team_id
        opponent = fx.away_team_id if home else fx.home_team_id
        out.append(s.FormMatch(
            kickoff_utc=fx.kickoff_utc, competition=fx.competition_id,
            opponent=names.get(opponent, opponent), venue="home" if home else "away",
            goals_for=res.home_goals if home else res.away_goals,
            goals_against=res.away_goals if home else res.home_goals))
    return out


def _implied(home: float, draw: float, away: float, at: datetime) -> s.Outcome:
    (p_home, p_draw, p_away), _ = devig(MarketOdds(home=home, draw=draw, away=away,
                                                   available_at=at))
    return s.Outcome(home=p_home, draw=p_draw, away=p_away)


def _odds_used(session: Session, run: PredictionRun, fx: Fixture, rnd: Super6Round
               ) -> s.OddsUsed | None:
    """The odds the forecast saw: the latest snapshot at the cutoff, or the round file's."""
    declared = next((f.get("odds") for f in rnd.spec.get("fixtures", [])
                     if f.get("odds") and ensure_utc(f["kickoff_utc"]) == fx.kickoff_utc
                     and {str(f.get("home")), str(f.get("away"))}
                     & {fx.home_team_id, fx.away_team_id}), None)
    snap = session.scalars(select(MarketSnapshot).where(
        MarketSnapshot.fixture_id == fx.id, MarketSnapshot.available_at <= run.cutoff_at)
        .order_by(MarketSnapshot.available_at.desc())).first()
    if declared is not None:
        at = ensure_utc(declared["available_at"])
        if at <= run.cutoff_at and (snap is None or at >= snap.available_at):
            return s.OddsUsed(
                source=str(declared.get("source", "manual")), odds_type="prematch",
                available_at=at, availability_basis="declared", home=declared["home"],
                draw=declared["draw"], away=declared["away"], over25=declared.get("over25"),
                under25=declared.get("under25"),
                implied=_implied(declared["home"], declared["draw"], declared["away"], at))
    if snap is None:
        return None
    return s.OddsUsed(
        source=f"{snap.source}:{snap.bookmaker}", odds_type=snap.odds_type,
        available_at=snap.available_at, availability_basis=snap.availability_basis,
        home=snap.home, draw=snap.draw, away=snap.away, over25=snap.over25,
        under25=snap.under25, implied=_implied(snap.home, snap.draw, snap.away,
                                               snap.available_at))


def _pair(values: Any) -> s.ExpectedGoals | None:
    if isinstance(values, (list, tuple)) and len(values) == 2:
        return s.ExpectedGoals(home=float(values[0]), away=float(values[1]))
    return None


def _summary(home: str, away: str, basis: str, ff: FixtureForecast, odds: s.OddsUsed | None,
             picks: Mapping[str, s.Pick], top: s.ScoreProbabilityOut | None) -> list[str]:
    def pct(p: float) -> str:
        return f"{100 * p:.1f}%"

    lines = []
    uses_market = basis in ("market odds", "blend")
    if uses_market and odds is not None:
        how = {"observed": "downloaded by this system at",
               "declared": "entered in the round file, as available at",
               "inferred": "assumed available (from the provider's schedule) at"}.get(
            odds.availability_basis, "available at")
        lines.append(f"This forecast is built from bookmaker odds ({odds.source}), {how} "
                     f"{odds.available_at:%d %b %Y %H:%M} UTC.")
        lines.append(f"With the bookmaker's margin removed, the odds imply {home} "
                     f"{pct(odds.implied.home)}, draw {pct(odds.implied.draw)}, {away} "
                     f"{pct(odds.implied.away)}.")
    elif basis == "results model":
        lines.append("No bookmaker odds were available before the cutoff, so this forecast "
                     "uses the results-based model (team strengths from past scores). It "
                     "backtests worse than the odds-based forecast.")
    else:
        lines.append(f"This forecast comes from the '{basis}' model.")
    lines.append(f"Expected goals: {home} {ff.lam_home:.2f}, {away} {ff.lam_away:.2f}.")
    team = _pair(ff.details.get("team_lambda"))
    if uses_market and team is not None:
        lines.append(f"The results-based model alone would expect {team.home:.2f} and "
                     f"{team.away:.2f}.")
    lines.append(f"Forecast: {home} win {pct(ff.p_home)}, draw {pct(ff.p_draw)}, {away} win "
                 f"{pct(ff.p_away)}.")
    if top is not None:
        lines.append(f"The most likely score is {top.home_goals}-{top.away_goals} "
                     f"({pct(top.probability)}), so it is still more likely to be wrong than "
                     "right.")
    points, jackpot = picks.get(Objective.POINTS.value), picks.get(Objective.JACKPOT.value)
    if points is not None:
        score = f"{points.predicted_home_goals}-{points.predicted_away_goals}"
        lines.append(f"Points mode picks {score}: the best expected points "
                     f"({points.expected_points:.2f}), with {pct(points.exact_score_probability)} "
                     f"for the exact score and {pct(points.correct_result_probability)} for the "
                     "right result.")
    if jackpot is not None:
        score = f"{jackpot.predicted_home_goals}-{jackpot.predicted_away_goals}"
        same = points is not None and (points.predicted_home_goals, points.predicted_away_goals
                                       ) == (jackpot.predicted_home_goals,
                                             jackpot.predicted_away_goals)
        lines.append(f"Jackpot mode picks {score}, the single most likely score"
                     + (" (the same pick)." if same else "."))
    return lines


def explanation(session: Session, run: PredictionRun, fixture_id: str, rules: CompetitionRules
                ) -> s.ExplanationResponse:
    ff, fx, names = _fixture_forecast(session, run, fixture_id)
    rnd = get_round(session, run.round_id)
    model_name, version = _model_version(session, run)
    basis = basis_from(model_name, ff.details)
    home = names.get(fx.home_team_id, fx.home_team_id)
    away = names.get(fx.away_team_id, fx.away_team_id)
    result = _results(session, [fixture_id]).get(fixture_id)
    picks = {r.mode: _pick(r, result, rules) for r in session.scalars(select(Recommendation).where(
        Recommendation.run_id == run.id, Recommendation.fixture_id == fixture_id))}
    cells = session.scalars(select(ScoreProbability).where(
        ScoreProbability.run_id == run.id, ScoreProbability.fixture_id == fixture_id)).all()
    top = _top_scores(cells, 1)
    odds = _odds_used(session, run, fx, rnd)
    return s.ExplanationResponse(
        fixture_id=fixture_id, run_id=run.id, round_id=run.round_id, home_team=home,
        away_team=away, model_version=version, forecast_basis=basis,
        summary=_summary(home, away, basis, ff, odds, picks, top[0] if top else None),
        expected_goals=s.ExpectedGoals(home=ff.lam_home, away=ff.lam_away),
        results_model_expected_goals=_pair(ff.details.get("team_lambda")),
        market_expected_goals=_pair(ff.details.get("market_lambda")),
        odds=odds if basis in ("market odds", "blend") else None,
        home_form=_form(session, fx.home_team_id, run.cutoff_at, names),
        away_form=_form(session, fx.away_team_id, run.cutoff_at, names), picks=picks,
        warnings=warning_notes([*ff.warnings,
                                *[w for w in run.warnings if str(w).startswith(fixture_id)]]))


# --------------------------------------------------------------------------- performance
def _live_round(session: Session, rnd: Super6Round) -> s.LiveRound | None:
    run = official_run(session, rnd.id)
    if run is None:
        return None
    evals = _evaluations(session, run.id)
    points, jackpot = evals.get("points"), evals.get("jackpot")
    if not points or not jackpot:
        return None
    forecast, golden = evals.get("forecast", {}), evals.get("golden_goal", {})
    expected = session.scalar(select(func.sum(Recommendation.expected_points)).where(
        Recommendation.run_id == run.id, Recommendation.mode == Objective.POINTS.value)) or 0.0
    actual = golden.get("actual_minute")
    source: Literal["official", "goal_minutes"] | None = None
    if actual is not None:
        source = "goal_minutes" if golden.get("from_goal_events") else "official"

    def whole(value: float | None) -> int | None:
        return None if value is None else int(value)

    return s.LiveRound(
        round_id=rnd.id, deadline_at=rnd.deadline_utc, run_id=run.id,
        record_type="locked" if run.status in FROZEN_STATUSES else "unlocked",
        fixtures_settled=int(points.get("fixtures_settled", 0)),
        points_mode_points=int(points["points"]), points_mode_exact=int(points["exact_hits"]),
        points_mode_expected=float(expected), jackpot_mode_points=int(jackpot["points"]),
        jackpot_mode_exact=int(jackpot["exact_hits"]),
        exact_log_loss_mean=forecast.get("exact_log_loss_mean"),
        rps_mean=forecast.get("rps_mean"), golden_goal_predicted=whole(
            golden.get("predicted_minute")), golden_goal_actual=whole(actual),
        golden_goal_abs_error=whole(golden.get("abs_error")), golden_goal_source=source)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _backtest(record: Mapping[str, Any]) -> s.Backtest | None:
    rows = [r for r in record.get("overall") or [] if "exact_log_loss" in r]
    window = record.get("window") or [None, None]
    if not rows or len(window) != 2:
        return None
    logged = record.get("logged_at")
    return s.Backtest(
        label=str(record.get("label")), logged_at=ensure_utc(logged) if logged else None,
        window=(str(window[0]), str(window[1])),
        competitions=[str(c) for c in record.get("competitions") or []],
        code_version=record.get("code_version"), reference=record.get("reference"),
        models=[s.BacktestModel(
            model=str(r["model"]), fixtures=int(r.get("fixtures", 0)),
            exact_log_loss=float(r["exact_log_loss"]), rps=r.get("rps"),
            points_per_fixture=r.get("points_points"),
            jackpot_points_per_fixture=r.get("jackpot_points"),
            exact_score_rate=r.get("jackpot_exact")) for r in rows])


def performance(session: Session, reports_dir: Path, default_model: str
                ) -> s.PerformanceResponse:
    rounds = [lr for rnd in session.scalars(select(Super6Round).order_by(
        Super6Round.deadline_utc.desc())) if (lr := _live_round(session, rnd)) is not None]
    golden = [float(r.golden_goal_abs_error) for r in rounds
              if r.golden_goal_abs_error is not None]
    totals = s.LiveTotals(
        rounds=len(rounds), locked_rounds=sum(r.record_type == "locked" for r in rounds),
        points_mode_mean=_mean([float(r.points_mode_points) for r in rounds]),
        points_mode_expected_mean=_mean([r.points_mode_expected for r in rounds]),
        jackpot_mode_mean=_mean([float(r.jackpot_mode_points) for r in rounds]),
        exact_hits_points_mode=sum(r.points_mode_exact for r in rounds),
        exact_hits_jackpot_mode=sum(r.jackpot_mode_exact for r in rounds),
        golden_goal_rounds=len(golden), golden_goal_abs_error_mean=_mean(golden))
    backtests = [b for rec in reversed(read_log(reports_dir))
                 if (b := _backtest(rec)) is not None][:20]
    with_default = [b for b in backtests if any(m.model == default_model for m in b.models)]
    headline = next((b for b in with_default if any(m.model in _BASELINES for m in b.models)),
                    with_default[0] if with_default else None)
    notes = [
        "Live rounds are real Super 6 rounds scored against real results. 'Unlocked' rounds "
        "had no forecast locked before the deadline, so they are indicative only.",
        "Backtests replay history match by match with only the information available at the "
        "time. Their odds timestamps are inferred from the provider's schedule, so they are "
        "strong approximations rather than proof of live performance.",
        "Forecasts are estimates, never guarantees.",
    ]
    return s.PerformanceResponse(live=s.LivePerformance(rounds=rounds, totals=totals),
                                 headline_backtest=headline, backtests=backtests, notes=notes)


# --------------------------------------------------------------------------- models
def models_summary(session: Session, defaults: Mapping[str, Any]) -> s.ModelsResponse:
    default = str(defaults.get("default_model", ""))
    configs: Mapping[str, Any] = defaults.get("models", {})
    default_cfg = configs.get(default, {})
    components = {str(default_cfg.get("team_model", "dixon_coles")),
                  str(default_cfg.get("market_model", "market_poisson"))
                  } if default == "ensemble" else set()

    def role(name: str) -> Literal["default", "component", "challenger", "baseline"]:
        if name == default:
            return "default"
        if name in components:
            return "component"
        return "baseline" if name in _BASELINES else "challenger"

    order = {"default": 0, "component": 1, "challenger": 2, "baseline": 3}
    models = sorted((s.ModelInfo(name=name, description=cls.description, role=role(name),
                                 config=dict(configs.get(name, {})))
                     for name, cls in MODEL_CLASSES.items()),
                    key=lambda m: (order[m.role], m.name))
    entries = session.scalars(select(ModelRegistryEntry)
                              .order_by(ModelRegistryEntry.created_at.desc()).limit(20)).all()
    return s.ModelsResponse(
        default_model=default, golden_goal=dict(defaults.get("golden_goal", {})), models=models,
        registry=[s.RegistryEntry(
            id=e.id, model_name=e.model_name, code_version=e.code_version,
            training_cutoff=e.training_cutoff, created_at=e.created_at,
            training_matches=e.summary.get("n_training_matches")) for e in entries],
        evidence="The default was promoted, and every challenger rejected, under rules "
                 "declared before each test. See docs/experiments/README.md.")


# --------------------------------------------------------------------------- health
def _recent_alerts(reports_dir: Path, now: datetime, days: float = 7.0, n: int = 10
                   ) -> list[s.AlertOut]:
    path = reports_dir / "alerts.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            at = ensure_utc(rec["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if rec.get("severity") in ("warning", "error") and now - at <= timedelta(days=days):
            out.append(s.AlertOut(severity=rec["severity"], code=str(rec.get("code")),
                                  message=str(rec.get("message")),
                                  round_id=rec.get("round_id"), at=at))
    return out[-n:][::-1]


def _last_cycle(reports_dir: Path) -> datetime | None:
    files = sorted((reports_dir / "cycle").glob("cycle-*.json"))
    if not files:
        return None
    try:
        return ensure_utc(json.loads(files[-1].read_text(encoding="utf-8"))["at"])
    except (ValueError, KeyError, TypeError):
        return None


def _attributions() -> list[str]:
    try:
        sources = load_yaml("data_sources.yaml").get("sources", {})
    except (OSError, ValueError):
        return []
    return [str(v["attribution"]) for v in sources.values() if v.get("attribution")]


def health(session: Session, *, rules: CompetitionRules, defaults: Mapping[str, Any],
           reports_dir: Path, admin_enabled: bool, now: datetime) -> s.HealthResponse:
    checks = [s.Check(name="database", status="ok", detail="reachable")]
    latest_result = session.scalar(select(func.max(FixtureResult.result_available_at)))
    if latest_result is None:
        checks.append(s.Check(name="results", status="error",
                              detail="no results ingested; run `super6 data ingest`"))
    else:
        age = (now - latest_result).days
        checks.append(s.Check(
            name="results", status="warning" if age > STALE_RESULTS_DAYS else "ok",
            detail=f"latest result is {age} day(s) old"))
    checks.append(s.Check(
        name="rules", status="ok" if rules.verified else "warning",
        detail=f"{rules.rules_version} "
               + ("checked against the official terms" if rules.verified else "NOT verified")))
    upcoming = session.scalars(select(Super6Round).where(Super6Round.deadline_utc >= now)
                               .order_by(Super6Round.deadline_utc)).first()
    if upcoming is None:
        checks.append(s.Check(name="current_round", status="warning",
                              detail="no upcoming round is stored"))
    else:
        run = official_run(session, upcoming.id)
        locked = run is not None and run.status in FROZEN_STATUSES
        ids = [link.fixture_id for link in upcoming.fixtures]
        with_odds = session.scalar(select(func.count(func.distinct(MarketSnapshot.fixture_id)))
                                   .where(MarketSnapshot.fixture_id.in_(ids),
                                          MarketSnapshot.available_at <= now)) or 0
        problems = []
        if not upcoming.spec.get("confirmed"):
            problems.append("fixtures not confirmed against the app")
        if run is None:
            problems.append("no forecast yet")
        if with_odds < len(ids):
            problems.append(f"bookmaker odds for {with_odds} of {len(ids)} fixtures")
        detail = f"{upcoming.id}: " + ("locked" if locked else "; ".join(problems) or "ready")
        checks.append(s.Check(name="current_round",
                              status="warning" if problems and not locked else "ok",
                              detail=detail))
    last_cycle = _last_cycle(reports_dir)
    if last_cycle is None:
        checks.append(s.Check(name="scheduler", status="warning",
                              detail="the hourly `super6 cycle` has never run here"))
    else:
        hours = (now - last_cycle).total_seconds() / 3600
        checks.append(s.Check(
            name="scheduler", status="warning" if hours > CYCLE_STALE_HOURS else "ok",
            detail=f"last cycle {hours:.1f} hour(s) ago"))
    alerts = _recent_alerts(reports_dir, now)
    errors = [a for a in alerts if a.severity == "error" and now - a.at <= timedelta(hours=24)]
    checks.append(s.Check(name="alerts", status="warning" if errors else "ok",
                          detail=f"{len(errors)} error alert(s) in the last 24 hours"))
    worst = {c.status for c in checks}
    return s.HealthResponse(
        status="error" if "error" in worst else "degraded" if "warning" in worst else "ok",
        generated_at=now, code_version=code_version(), rules_version=rules.rules_version,
        rules_verified=rules.verified, default_model=str(defaults.get("default_model", "")),
        golden_goal_profile=str(defaults.get("golden_goal", {}).get("profile", "uniform")),
        admin_enabled=admin_enabled, checks=checks, latest_result_at=latest_result,
        sources=[s.SourceFreshness(source=src, snapshots=n, latest_retrieved_at=latest)
                 for src, n, latest in session.execute(
                     select(SourceSnapshot.source, func.count(),
                            func.max(SourceSnapshot.retrieved_at))
                     .group_by(SourceSnapshot.source).order_by(SourceSnapshot.source))],
        odds=[s.OddsCoverage(odds_type=t, availability_basis=b, snapshots=n, fixtures=f)
              for t, b, n, f in session.execute(
                  select(MarketSnapshot.odds_type, MarketSnapshot.availability_basis,
                         func.count(), func.count(func.distinct(MarketSnapshot.fixture_id)))
                  .group_by(MarketSnapshot.odds_type, MarketSnapshot.availability_basis)
                  .order_by(MarketSnapshot.odds_type, MarketSnapshot.availability_basis))],
        goal_events=session.scalar(select(func.count(GoalEvent.id))) or 0,
        quality_issues={f"{sev}:{code}": n for sev, code, n in session.execute(
            select(DataQualityIssue.severity, DataQualityIssue.code, func.count())
            .group_by(DataQualityIssue.severity, DataQualityIssue.code))},
        last_cycle_at=last_cycle, recent_alerts=alerts, attributions=_attributions())
