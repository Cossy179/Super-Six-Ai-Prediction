"""The unattended round workflow, driven through a whole round with a fake clock."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml
from sqlalchemy import Engine, func, select

from super6.features.point_in_time import MatchHistory
from super6.ingestion.round_sources import SourceReport
from super6.jobs.alerts import AlertSink
from super6.jobs.cycle import CycleDeps, run_cycle
from super6.models import build_model
from super6.rules import CompetitionRules
from super6.storage.db import session_scope
from super6.storage.models import (
    Competition,
    Evaluation,
    FixtureResult,
    GoalEvent,
    PredictionRun,
    RunStatus,
    Super6Round,
    Team,
)
from super6.synthetic import SyntheticTruth
from super6.timeutil import UK, iso

PAIRS = [(f"team-{2 * i:02d}", f"team-{2 * i + 1:02d}") for i in range(6)]


@pytest.fixture()
def world(engine: Engine, league: tuple[MatchHistory, SyntheticTruth], tmp_path: Path
          ) -> dict[str, Any]:
    history, _ = league
    with session_scope(engine) as s:
        s.add(Competition(id="SYN", name="Synthetic", country="-", pool="syn", tier=1))
        for t in sorted(set(history.matches["home_team"])):
            s.add(Team(id=t, canonical_name=t))
    last = history.matches["kickoff_utc"].max().tz_convert(UK)
    saturday = (last + pd.Timedelta(days=7)).date()
    kickoff = datetime(saturday.year, saturday.month, saturday.day, 15, tzinfo=UK).astimezone(UTC)
    rounds = tmp_path / "rounds"
    rounds.mkdir()
    return {"history": history, "kickoff": kickoff, "rounds": rounds, "tmp": tmp_path}


def _write_round(world: dict[str, Any], rules: CompetitionRules, round_id: str = "test-r1") -> Path:
    spec = {"round_id": round_id, "deadline_utc": iso(world["kickoff"]),
            "rules_version": rules.rules_version, "illustrative": False,
            "confirmed": True, "confirmed_at": iso(world["kickoff"] - timedelta(days=3)),
            "confirmed_source": "test",
            "fixtures": [{"home": h, "away": a, "competition": "SYN",
                          "kickoff_utc": iso(world["kickoff"])} for h, a in PAIRS]}
    path = world["rounds"] / f"{round_id}.yaml"
    path.write_text(yaml.safe_dump(spec))
    return path


def _deps(world: dict[str, Any], history: MatchHistory | None = None,
          lines: list[str] | None = None) -> CycleDeps:
    hist = history if history is not None else world["history"]
    return CycleDeps(
        refresh=lambda session, now: [],
        load_history=lambda session: hist,
        fetch_round_lines=lambda d: SourceReport(lines=lines or [], articles=[
            {"url": "https://example.test/preview", "super6_matches": len(lines or [])}]),
        model_factory=lambda: build_model("poisson"),
        rounds_dir=world["rounds"],
        known_competitions={"SYN"},
        golden_goal_config={"n_simulations": 1000},
    )


def _sink(world: dict[str, Any], now: list[datetime]) -> AlertSink:
    return AlertSink(world["tmp"] / "alerts.jsonl", webhook_url="", now=lambda: now[0])


def _runs(engine: Engine) -> list[PredictionRun]:
    with session_scope(engine) as s:
        return list(s.scalars(select(PredictionRun)))


def test_full_round_from_forecast_to_settlement(engine: Engine, rules: CompetitionRules,
                                                world: dict[str, Any]) -> None:
    _write_round(world, rules)
    ko = world["kickoff"]
    clock = [ko - timedelta(days=2)]
    sink, deps = _sink(world, clock), _deps(world)

    first = run_cycle(engine, rules, deps, sink, clock[0]).rounds[0]
    assert first.actions == ["forecast saved and published"]
    assert first.status == RunStatus.PUBLISHED.value and len(first.picks) == 7

    clock[0] += timedelta(hours=1)  # nothing new: no new run
    assert run_cycle(engine, rules, deps, sink, clock[0]).rounds[0].actions == [
        "forecast unchanged"]
    assert len(_runs(engine)) == 1

    clock[0] = ko - timedelta(minutes=30)  # inside the lock window
    locked = run_cycle(engine, rules, deps, sink, clock[0]).rounds[0]
    assert locked.actions == ["forecast unchanged", "locked"]
    assert any(a.code == "ROUND_LOCKED" for a in sink.emitted)
    clock[0] = ko - timedelta(minutes=5)
    assert run_cycle(engine, rules, deps, sink, clock[0]).rounds[0].actions == ["already locked"]

    clock[0] = ko + timedelta(hours=1)  # matches in progress
    assert "waiting for matches to finish" in run_cycle(engine, rules, deps, sink,
                                                        clock[0]).rounds[0].actions
    with session_scope(engine) as s:
        rnd = s.get(Super6Round, "test-r1")
        assert rnd is not None
        for i, rf in enumerate(rnd.fixtures):
            s.add(FixtureResult(fixture_id=rf.fixture_id, source="test", home_goals=i % 3,
                                away_goals=1, result_available_at=ko + timedelta(hours=2)))
            # Goal minutes: each match's away goal at 11 + i, home goals after the break.
            s.add(GoalEvent(fixture_id=rf.fixture_id, source="test", period=1,
                            match_minute=11 + i, stoppage_minute=None, team_side="away"))
            for k in range(i % 3):
                s.add(GoalEvent(fixture_id=rf.fixture_id, source="test", period=2,
                                match_minute=50 + k, stoppage_minute=None, team_side="home"))
    clock[0] = ko + timedelta(hours=4)
    settled = run_cycle(engine, rules, deps, sink, clock[0]).rounds[0]
    assert "settled" in settled.actions and settled.status == RunStatus.SETTLED.value
    assert any(a.code == "ROUND_SETTLED" for a in sink.emitted)
    with session_scope(engine) as s:
        n_evals = s.scalar(select(func.count(Evaluation.id)))
    clock[0] += timedelta(hours=1)  # idempotent afterwards
    assert run_cycle(engine, rules, deps, sink, clock[0]).rounds[0].actions == ["settled"]
    with session_scope(engine) as s:
        assert s.scalar(select(func.count(Evaluation.id))) == n_evals
    assert not sink.has_errors

    def gg_eval(metric: str) -> float | None:
        with session_scope(engine) as s:
            return s.scalar(select(Evaluation.value).where(
                Evaluation.run_id == settled.run_id, Evaluation.mode == "golden_goal",
                Evaluation.metric == metric))

    # Settled automatically from the stored goal minutes: the earliest is minute 11.
    assert gg_eval("actual_minute") == 11 and gg_eval("from_goal_events") == 1
    # Later, the player records the official Golden Goal minute, which takes precedence.
    from super6.prediction.service import settle_round

    with session_scope(engine) as s:
        locked_run = settled.run_id
        out = settle_round(s, "test-r1", rules, golden_goal_minute=7)
        gg = next(r for r in out if r.run_id == locked_run).metrics["golden_goal"]
        assert gg["actual_minute"] == 7 and gg["abs_error"] == abs(gg["predicted_minute"] - 7)
    assert gg_eval("actual_minute") == 7 and gg_eval("from_goal_events") == 0
    # Goal minutes say 11 under either reading, so the official 7 matches neither.
    assert gg_eval("official_matches_earliest_match_minute") == 0
    assert gg_eval("official_matches_first_in_real_time") == 0
    with session_scope(engine) as s:
        assert s.scalar(select(func.count(Evaluation.id)).where(
            Evaluation.mode == "golden_goal")) >= 3
    with pytest.raises(ValueError), session_scope(engine) as s:
        settle_round(s, "test-r1", rules, golden_goal_minute=95)


def test_new_information_creates_a_new_run(engine: Engine, rules: CompetitionRules,
                                           world: dict[str, Any]) -> None:
    _write_round(world, rules)
    ko = world["kickoff"]
    clock = [ko - timedelta(days=2)]
    sink = _sink(world, clock)
    run_cycle(engine, rules, _deps(world), sink, clock[0])
    # One more result becomes public: team strengths move, so the forecast changes.
    h = world["history"]
    extra = h.matches.iloc[[-1]].copy()
    extra["fixture_id"] = "extra-match"
    extra["kickoff_utc"] = ko - timedelta(days=1, hours=3)
    extra["result_available_at"] = ko - timedelta(days=1)
    extra[["home_goals", "away_goals"]] = [6, 0]
    clock[0] = ko - timedelta(hours=12)
    out = run_cycle(engine, rules, _deps(world, h.with_extra(matches=extra)), sink,
                    clock[0]).rounds[0]
    assert out.actions == ["forecast saved and published"]
    assert len(_runs(engine)) == 2


def test_missed_lock_and_void_fixtures(engine: Engine, rules: CompetitionRules,
                                       world: dict[str, Any]) -> None:
    _write_round(world, rules, "test-late")
    ko = world["kickoff"]
    clock = [ko + timedelta(hours=1)]  # first run after the deadline: too late to lock
    sink = _sink(world, clock)
    out = run_cycle(engine, rules, _deps(world), sink, clock[0]).rounds[0]
    assert "missed lock" in out.actions and sink.has_errors
    assert any(a.code == "ROUND_NOT_LOCKED" for a in sink.emitted)

    _write_round(world, rules, "test-voidy")
    (world["rounds"] / "test-late.yaml").unlink()
    clock[0] = ko - timedelta(minutes=10)
    run_cycle(engine, rules, _deps(world), sink, clock[0])
    with session_scope(engine) as s:
        rnd = s.get(Super6Round, "test-voidy")
        assert rnd is not None
        for rf in rnd.fixtures[:5]:  # one match postponed
            s.add(FixtureResult(fixture_id=rf.fixture_id, source="test", home_goals=1,
                                away_goals=1, result_available_at=ko + timedelta(hours=2)))
    clock[0] = ko + timedelta(days=1)
    assert any("waiting for results" in a for a in run_cycle(
        engine, rules, _deps(world), sink, clock[0]).rounds[0].actions)
    clock[0] = ko + timedelta(days=4)
    out = run_cycle(engine, rules, _deps(world), sink, clock[0]).rounds[0]
    assert "settled" in out.actions
    assert any(a.code == "VOID_FIXTURES" for a in sink.emitted)


def test_kickoff_change_rewrites_the_round(engine: Engine, rules: CompetitionRules,
                                           world: dict[str, Any]) -> None:
    path = _write_round(world, rules)
    ko = world["kickoff"]
    h = world["history"]
    listed = h.matches.iloc[[-1]].copy()  # the provider now lists the first pairing later
    listed["fixture_id"] = "provider-listing"
    listed["home_team"], listed["away_team"] = PAIRS[0]
    listed["kickoff_utc"] = ko + timedelta(hours=2, minutes=30)
    listed["status"] = "SCHEDULED"
    listed[["home_goals", "away_goals", "result_available_at"]] = None
    clock = [ko - timedelta(days=1)]
    sink = _sink(world, clock)
    run_cycle(engine, rules, _deps(world, h.with_extra(matches=listed)), sink, clock[0])
    assert any(a.code == "KICKOFF_CHANGED" for a in sink.emitted)
    data = yaml.safe_load(path.read_text())
    assert data["fixtures"][0]["kickoff_utc"] == iso(ko + timedelta(hours=2, minutes=30))
    assert data["deadline_utc"] == iso(ko)  # other games still kick off first


def test_missing_round_is_fetched_from_previews(engine: Engine, rules: CompetitionRules,
                                                world: dict[str, Any]) -> None:
    ko = world["kickoff"]
    lines = [f"{h} v {a}, Saturday 3pm" for h, a in PAIRS]
    clock = [ko - timedelta(days=2)]
    sink = _sink(world, clock)
    out = run_cycle(engine, rules, _deps(world, lines=lines), sink, clock[0])
    assert any(a.code == "ROUND_FETCHED" for a in sink.emitted)
    assert len(list(world["rounds"].glob("*.yaml"))) == 1
    assert out.rounds[0].actions == ["forecast saved and published", "unconfirmed: not locking"]
    # Fewer than six found: warn, write nothing.
    for f in world["rounds"].glob("*.yaml"):
        f.unlink()
    sink2 = AlertSink(world["tmp"] / "alerts2.jsonl", webhook_url="", now=lambda: clock[0])
    run_cycle(engine, rules, _deps(world, lines=lines[:4]), sink2, clock[0])
    assert any(a.code == "ROUND_NOT_FOUND" for a in sink2.emitted)
    assert list(world["rounds"].glob("*.yaml")) == []


def test_refresh_failure_is_reported_not_fatal(engine: Engine, rules: CompetitionRules,
                                               world: dict[str, Any]) -> None:
    _write_round(world, rules)
    clock = [world["kickoff"] - timedelta(days=2)]
    sink = _sink(world, clock)
    deps = _deps(world)

    def boom(session: Any, now: datetime) -> list[str]:
        raise ConnectionError("provider unreachable")

    deps.refresh = boom
    out = run_cycle(engine, rules, deps, sink, clock[0])
    assert any(a.code == "REFRESH_FAILED" for a in sink.emitted)
    assert out.rounds[0].actions == ["forecast saved and published"]


def test_alert_dedupe_and_webhook(tmp_path: Path) -> None:
    posted: list[dict[str, str]] = []
    now = datetime(2026, 11, 7, 12, tzinfo=UTC)
    sink = AlertSink(tmp_path / "a.jsonl", webhook_url="https://hooks.example.test/x",
                     post=lambda url, payload: posted.append(payload), now=lambda: now)
    assert sink.emit("warning", "ROUND_NOT_FOUND", "nothing yet", "r1") is not None
    assert sink.emit("warning", "ROUND_NOT_FOUND", "nothing yet", "r1") is None  # deduped
    sink.emit("info", "ROUND_LOCKED", "locked", "r1")  # info: logged, not posted
    assert len(posted) == 1 and "ROUND_NOT_FOUND" in posted[0]["text"]
    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    assert [json.loads(x)["code"] for x in lines] == ["ROUND_NOT_FOUND", "ROUND_LOCKED"]


def test_fetched_rounds_are_not_locked_until_confirmed(engine: Engine, rules: CompetitionRules,
                                                        world: dict[str, Any]) -> None:
    ko = world["kickoff"]
    lines = [f"{h} v {a}, Saturday 3pm" for h, a in PAIRS]
    clock = [ko - timedelta(minutes=30)]  # already inside the lock window
    sink = _sink(world, clock)
    out = run_cycle(engine, rules, _deps(world, lines=lines), sink, clock[0]).rounds[0]
    assert "unconfirmed: not locking" in out.actions and out.status != RunStatus.LOCKED.value
    assert any(a.code == "ROUND_UNCONFIRMED" and a.severity == "error" for a in sink.emitted)
    (path,) = world["rounds"].glob("*.yaml")
    assert yaml.safe_load(path.read_text())["confirmed"] is False
