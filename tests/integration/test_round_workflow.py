"""End-to-end: round file → forecast → persist → lock (immutable) → settle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.exc import DBAPIError

from super6.features.point_in_time import MatchHistory
from super6.ingestion.canonicalize import TeamRegistry
from super6.models import build_model
from super6.models.base import CutoffAfterKickoffError
from super6.prediction.round_spec import OddsInput, RoundSpec, resolve_round
from super6.prediction.service import (
    LockError,
    forecast_round,
    forecast_to_json,
    lock_run,
    persist_forecast,
    settle_round,
)
from super6.rules import CompetitionRules
from super6.storage.db import session_scope
from super6.storage.models import (
    Competition,
    FixtureResult,
    ImmutableRecordError,
    PredictionRun,
    Recommendation,
    RunStatus,
    ScoreProbability,
    Team,
)
from super6.synthetic import SyntheticTruth


def _setup(engine: Engine, history: MatchHistory) -> TeamRegistry:
    reg = TeamRegistry()
    with session_scope(engine) as s:
        s.add(Competition(id="SYN", name="Synthetic League", country="Nowhere", pool="syn",
                          tier=1))
        for t in sorted(set(history.matches["home_team"])):
            s.add(Team(id=t, canonical_name=t.replace("-", " ").title()))
            reg.add_team(t, t.replace("-", " ").title())
    return reg


def _spec(rules: CompetitionRules, kickoff: datetime, n: int = 6, **extra: object) -> RoundSpec:
    pairs = [(f"team-{2 * i:02d}", f"team-{2 * i + 1:02d}") for i in range(n)]
    payload = {
        "round_id": "test-round-1",
        "deadline_utc": kickoff,
        "rules_version": rules.rules_version,
        "illustrative": False,
        "fixtures": [{"home": h, "away": a, "competition": "SYN", "kickoff_utc": kickoff}
                     for h, a in pairs],
    }
    payload.update(extra)
    return RoundSpec.model_validate(payload)


def test_full_round_lifecycle(engine: Engine, rules: CompetitionRules,
                              league: tuple[MatchHistory, SyntheticTruth]) -> None:
    history, _ = league
    reg = _setup(engine, history)
    kickoff = (history.matches["kickoff_utc"].max() + pd.Timedelta(days=7)).to_pydatetime()
    spec = _spec(rules, kickoff)
    resolved = resolve_round(spec, reg, rules, {"SYN"})
    assert resolved.block_reasons == []
    cutoff = kickoff - timedelta(hours=2)
    model = build_model("dixon_coles")
    fc = forecast_round(history, resolved, cutoff, model, rules, {"n_simulations": 5000})
    assert fc.status is RunStatus.VALIDATED
    assert "RULES_UNVERIFIED" not in fc.warnings
    payload = forecast_to_json(fc, reg)
    assert len(payload["modes"]["points"]["fixtures"]) == 6
    assert payload["golden_goal"]["method"] == "uniform-hazard-poisson-simulation"
    # Deterministic: same inputs → same fingerprint and probabilities.
    again = forecast_round(history, resolved, cutoff, model, rules, {"n_simulations": 5000})
    assert again.run_id == fc.run_id
    assert all((a.distribution.grid == b.distribution.grid).all()
               for a, b in zip(fc.predictions, again.predictions, strict=True))

    with session_scope(engine) as s:
        run = persist_forecast(s, resolved, fc, publish=True)
        assert run.status == RunStatus.PUBLISHED.value
        assert persist_forecast(s, resolved, again).id == run.id  # idempotent
        n_cells = len(s.scalars(select(ScoreProbability).where(
            ScoreProbability.run_id == run.id)).all())
        assert n_cells == sum(p.distribution.size ** 2 for p in fc.predictions)

    with session_scope(engine) as s:
        with pytest.raises(LockError):
            lock_run(s, fc.run_id, now=kickoff + timedelta(minutes=5))  # after deadline
        lock_run(s, fc.run_id, now=cutoff)

    with pytest.raises(ImmutableRecordError), session_scope(engine) as s:
        rec = s.scalar(select(Recommendation).where(Recommendation.run_id == fc.run_id))
        assert rec is not None
        rec.home_goals += 1
    with pytest.raises(ImmutableRecordError), session_scope(engine) as s:
        cell = s.scalar(select(ScoreProbability).where(ScoreProbability.run_id == fc.run_id))
        s.delete(cell)
    with pytest.raises(ImmutableRecordError), session_scope(engine) as s:
        locked = s.get(PredictionRun, fc.run_id)
        assert locked is not None
        locked.status = RunStatus.PUBLISHED.value

    # The database itself refuses bulk statements that bypass the ORM guard.
    for stmt in (
        update(Recommendation).where(Recommendation.run_id == fc.run_id).values(home_goals=9),
        delete(ScoreProbability).where(ScoreProbability.run_id == fc.run_id),
        update(PredictionRun).where(PredictionRun.id == fc.run_id).values(cutoff_at=kickoff),
        delete(PredictionRun).where(PredictionRun.id == fc.run_id),
    ):
        with pytest.raises(DBAPIError), session_scope(engine) as s:
            s.execute(stmt, execution_options={"synchronize_session": False})

    # Settle: results for five fixtures only → waits; then the sixth arrives.
    with session_scope(engine) as s:
        for i, fx in enumerate(fc.fixtures[:5]):
            s.add(FixtureResult(fixture_id=fx.fixture_id, source="test", home_goals=i % 3,
                                away_goals=1, result_available_at=kickoff + timedelta(hours=2)))
    with session_scope(engine) as s:
        waiting = settle_round(s, "test-round-1", rules)
        assert waiting[0].missing_fixtures == [fc.fixtures[5].fixture_id]
        assert s.get(PredictionRun, fc.run_id).status == RunStatus.LOCKED.value  # type: ignore[union-attr]
    with session_scope(engine) as s:
        s.add(FixtureResult(fixture_id=fc.fixtures[5].fixture_id, source="test", home_goals=2,
                            away_goals=2, result_available_at=kickoff + timedelta(hours=2)))
    with session_scope(engine) as s:
        settled = settle_round(s, "test-round-1", rules)
        assert settled[0].status == RunStatus.SETTLED.value
        pts = settled[0].metrics["points"]
        assert 0 <= pts["points"] <= 30 and pts["fixtures_settled"] == 6


def test_round_checks_block_bad_rounds(rules: CompetitionRules,
                                       league: tuple[MatchHistory, SyntheticTruth]) -> None:
    history, _ = league
    reg = TeamRegistry()
    for t in sorted(set(history.matches["home_team"])):
        reg.add_team(t, t)
    ko = datetime(2030, 1, 5, 15, tzinfo=UTC)
    short = resolve_round(_spec(rules, ko, n=5), reg, rules, {"SYN"})
    assert any(b.startswith("WRONG_FIXTURE_COUNT") for b in short.block_reasons)
    late = resolve_round(_spec(rules, ko, deadline_utc=ko + timedelta(hours=1)), reg, rules,
                         {"SYN"})
    assert any(b.startswith("DEADLINE_AFTER_KICKOFF") for b in late.block_reasons)
    wrong_rules = resolve_round(_spec(rules, ko, rules_version="other"), reg, rules, {"SYN"})
    assert any(b.startswith("RULES_VERSION_MISMATCH") for b in wrong_rules.block_reasons)
    spec = _spec(rules, ko)
    bad = spec.model_copy(update={"fixtures": [
        spec.fixtures[0].model_copy(update={"home": "Not A Team"}), *spec.fixtures[1:]]})
    unknown = resolve_round(bad, reg, rules, {"SYN"})
    assert any(b.startswith("UNKNOWN_TEAM") for b in unknown.block_reasons)
    dup = spec.model_copy(update={"fixtures": [spec.fixtures[0], spec.fixtures[0],
                                               *spec.fixtures[2:]]})
    repeated = resolve_round(dup, reg, rules, {"SYN"})
    assert any(b.startswith("TEAM_IN_MULTIPLE_FIXTURES") for b in repeated.block_reasons)


def test_cutoff_after_deadline_blocks(rules: CompetitionRules,
                                      league: tuple[MatchHistory, SyntheticTruth]) -> None:
    history, _ = league
    reg = TeamRegistry()
    for t in sorted(set(history.matches["home_team"])):
        reg.add_team(t, t)
    kickoff = (history.matches["kickoff_utc"].max() + pd.Timedelta(days=7)).to_pydatetime()
    spec = _spec(rules, kickoff, deadline_utc=kickoff - timedelta(hours=3))
    resolved = resolve_round(spec, reg, rules, {"SYN"})
    fc = forecast_round(history, resolved, kickoff - timedelta(hours=1), build_model("poisson"),
                        rules, {"n_simulations": 1000})
    assert fc.status is RunStatus.BLOCKED
    assert any(b.startswith("CUTOFF_AFTER_DEADLINE") for b in fc.block_reasons)
    with pytest.raises(CutoffAfterKickoffError):
        forecast_round(history, resolved, kickoff, build_model("poisson"), rules,
                       {"n_simulations": 1000})


def test_round_file_odds_drive_the_ensemble(rules: CompetitionRules,
                                            league: tuple[MatchHistory, SyntheticTruth]) -> None:
    history, _ = league
    reg = TeamRegistry()
    for t in sorted(set(history.matches["home_team"])):
        reg.add_team(t, t)
    kickoff = (history.matches["kickoff_utc"].max() + pd.Timedelta(days=7)).to_pydatetime()
    spec = _spec(rules, kickoff)
    odds = {"home": 1.5, "draw": 4.2, "away": 6.5, "over25": 1.7, "under25": 2.2,
            "available_at": kickoff - timedelta(hours=20)}
    late_odds = {**odds, "available_at": kickoff - timedelta(minutes=30)}  # after the cutoff
    fixtures = [spec.fixtures[0].model_copy(update={"odds": OddsInput.model_validate(odds)}),
                spec.fixtures[1].model_copy(update={"odds": OddsInput.model_validate(late_odds)}),
                *spec.fixtures[2:]]
    resolved = resolve_round(spec.model_copy(update={"fixtures": fixtures}), reg, rules, {"SYN"})
    fc = forecast_round(history, resolved, kickoff - timedelta(hours=2), build_model("ensemble"),
                        rules, {"n_simulations": 1000})
    payload = forecast_to_json(fc, reg)
    basis = [f["forecast_basis"] for f in payload["fixtures"]]
    assert basis[0] == "market odds"
    assert basis[1:] == ["results model"] * 5  # late odds are invisible at the cutoff
    first = payload["fixtures"][0]["outcome_probabilities"]
    assert first["home"] > 0.55  # 1.5 home odds ≈ 63% after removing the margin


def test_staggered_kickoffs_report_the_alternative_golden_goal(
    rules: CompetitionRules, league: tuple[MatchHistory, SyntheticTruth]
) -> None:
    history, _ = league
    reg = TeamRegistry()
    for t in sorted(set(history.matches["home_team"])):
        reg.add_team(t, t)
    kickoff = (history.matches["kickoff_utc"].max() + pd.Timedelta(days=7)).to_pydatetime()
    early = kickoff - timedelta(hours=2, minutes=30)
    spec = _spec(rules, kickoff)
    fixtures = [spec.fixtures[0].model_copy(update={"kickoff_utc": early}), *spec.fixtures[1:]]
    staggered = spec.model_copy(update={"fixtures": fixtures, "deadline_utc": early})
    resolved = resolve_round(staggered, reg, rules, {"SYN"})
    assert resolved.block_reasons == []
    fc = forecast_round(history, resolved, early - timedelta(hours=1), build_model("poisson"),
                        rules, {"n_simulations": 5000})
    warning = [w for w in fc.warnings if w.startswith("GOLDEN_GOAL_STAGGERED_KICKOFFS")]
    assert len(warning) == 1 and "first_in_real_time" in warning[0]
    # The match-clock reading ignores the early kick-off, so the pick stays early.
    assert fc.golden_goal is not None and fc.golden_goal.minute <= 7
    # A deadline before the first kick-off is allowed but flagged.
    earlier = resolve_round(spec.model_copy(update={"deadline_utc": kickoff - timedelta(hours=1)}),
                            reg, rules, {"SYN"})
    assert any(w.startswith("DEADLINE_BEFORE_FIRST_KICKOFF") for w in earlier.warnings)


def test_illustrative_rounds_are_never_saved(engine: Engine, rules: CompetitionRules,
                                             league: tuple[MatchHistory, SyntheticTruth]
                                             ) -> None:
    from super6.prediction.service import IllustrativeRoundError
    from super6.storage.models import Fixture, Super6Round

    history, _ = league
    reg = _setup(engine, history)
    kickoff = (history.matches["kickoff_utc"].max() + pd.Timedelta(days=7)).to_pydatetime()
    spec = _spec(rules, kickoff, illustrative=True)
    resolved = resolve_round(spec, reg, rules, {"SYN"})
    assert "ILLUSTRATIVE_DATA_ONLY" in resolved.warnings
    fc = forecast_round(history, resolved, kickoff - timedelta(hours=2), build_model("poisson"),
                        rules, {"n_simulations": 1000})
    with pytest.raises(IllustrativeRoundError), session_scope(engine) as s:
        persist_forecast(s, resolved, fc)
    with session_scope(engine) as s:
        assert s.scalar(select(Super6Round.id)) is None
        assert s.scalar(select(Fixture.id)) is None
