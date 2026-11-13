"""The REST API end to end: import → forecast → confirm → lock → settle, all over HTTP."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from super6.api.app import create_app
from super6.api.deps import MAX_FAILED_AUTH, ApiContext
from super6.api.queries import warning_notes
from super6.features.point_in_time import MatchHistory
from super6.models import build_model
from super6.rules import CompetitionRules
from super6.storage.db import session_scope
from super6.storage.models import Base, Competition, Fixture, FixtureResult, Team
from super6.synthetic import SyntheticTruth
from super6.timeutil import UK, iso

TOKEN = "test-admin-token-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MATCHUPS = ", ".join(f"team-{2 * i:02d} v team-{2 * i + 1:02d}" for i in range(6))
ROUND = "super6-2022-23-round-07"  # the synthetic league ends in spring 2023


@pytest.fixture()
def api(engine: Engine, league: tuple[MatchHistory, SyntheticTruth], rules: CompetitionRules,
        tmp_path: Path) -> dict[str, Any]:
    history, _ = league
    m = history.matches
    with session_scope(engine) as s:
        s.add(Competition(id="SYN", name="Synthetic", country="-", pool="syn", tier=1))
        for t in sorted(set(m["home_team"])):
            s.add(Team(id=t, canonical_name=t.replace("-", " ").title()))
        s.flush()
        for r in m.itertuples():  # results in the database, as ingestion would leave them
            s.add(Fixture(id=r.fixture_id, competition_id="SYN", season=r.season,
                          home_team_id=r.home_team, away_team_id=r.away_team,
                          kickoff_utc=r.kickoff_utc.to_pydatetime(), kickoff_time_known=True,
                          neutral=False, status="FINISHED", source="test"))
            s.add(FixtureResult(fixture_id=r.fixture_id, source="test",
                                home_goals=int(r.home_goals), away_goals=int(r.away_goals),
                                result_available_at=r.result_available_at.to_pydatetime()))
    last = m["kickoff_utc"].max().tz_convert(UK)
    saturday = (last + pd.Timedelta(days=7)).date()
    kickoff = datetime(saturday.year, saturday.month, saturday.day, 15, tzinfo=UK).astimezone(UTC)
    clock = [kickoff - timedelta(days=2)]
    ctx = ApiContext(
        engine=engine, rules=rules, rounds_dir=tmp_path / "rounds",
        reports_dir=tmp_path / "reports", known_competitions={"SYN"},
        model_defaults={"default_model": "ensemble", "models": {},
                        "golden_goal": {"profile": "uniform"}},
        load_history=lambda session: history, model_factory=lambda: build_model("ensemble"),
        admin_token=TOKEN, now=lambda: clock[0])
    return {"client": TestClient(create_app(ctx)), "ctx": ctx, "clock": clock,
            "kickoff": kickoff, "saturday": saturday, "history": history}


def _import(api: dict[str, Any], **extra: Any) -> Any:
    body = {"text": MATCHUPS, "round_number": 7, "round_date": api["saturday"].isoformat()}
    return api["client"].post("/api/v1/admin/rounds/import", json=body | extra, headers=AUTH)


def test_empty_service_is_honest_about_it(api: dict[str, Any]) -> None:
    c: TestClient = api["client"]
    health = c.get("/api/v1/health")
    assert health.status_code == 200
    body = health.json()
    checks = {x["name"]: x for x in body["checks"]}
    assert body["status"] == "degraded" and body["admin_enabled"] is True
    assert checks["database"]["status"] == "ok"
    assert checks["current_round"]["detail"] == "no upcoming round is stored"
    assert checks["scheduler"]["status"] == "warning"
    assert body["attributions"]  # provider attributions are always shown
    assert c.get("/api/v1/rounds/current").status_code == 404
    assert c.get("/api/v1/rounds").json() == []
    perf = c.get("/api/v1/performance").json()
    assert perf["live"]["rounds"] == [] and perf["live"]["totals"]["rounds"] == 0
    assert any("never guarantees" in n for n in perf["notes"])
    models = c.get("/api/v1/models").json()
    roles = {x["name"]: x["role"] for x in models["models"]}
    assert roles["ensemble"] == "default" and roles["dixon_coles"] == "component"
    assert roles["league_average"] == "baseline" and roles["catboost"] == "challenger"


def test_openapi_lists_the_designed_endpoints(api: dict[str, Any]) -> None:
    paths = set(api["client"].get("/api/openapi.json").json()["paths"])
    assert {"/api/v1/health", "/api/v1/rounds/current", "/api/v1/rounds/{round_id}",
            "/api/v1/rounds/{round_id}/predictions",
            "/api/v1/fixtures/{fixture_id}/distribution",
            "/api/v1/fixtures/{fixture_id}/explanation", "/api/v1/performance",
            "/api/v1/models", "/api/v1/admin/rounds/import",
            "/api/v1/admin/predictions/generate"} <= paths


def test_admin_needs_the_token_and_guessing_is_throttled(api: dict[str, Any]) -> None:
    c: TestClient = api["client"]
    url = "/api/v1/admin/predictions/generate"
    body = {"round_id": ROUND}
    assert c.post(url, json=body).status_code == 401
    assert c.post(url, json=body, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.post(url, json=body, headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401
    assert c.post(url, json=body, headers=AUTH).status_code == 404  # authorised; no such round
    for _ in range(MAX_FAILED_AUTH):
        c.post(url, json=body, headers={"Authorization": "Bearer guess"})
    assert c.post(url, json=body, headers=AUTH).status_code == 429  # even the right token waits
    api["ctx"].admin_token = None
    api["ctx"]._failed_auth.clear()
    assert c.post(url, json=body, headers=AUTH).status_code == 503  # admin switched off


def test_a_round_from_import_to_settlement(api: dict[str, Any]) -> None:
    c: TestClient = api["client"]
    clock, kickoff = api["clock"], api["kickoff"]
    path = api["ctx"].rounds_dir / f"{ROUND}.yaml"

    # --- import: found, stored, but not confirmed
    imported = _import(api)
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["round_id"] == ROUND and body["confirmed"] is False
    assert len(body["fixtures"]) == 6 and body["block_reasons"] == []
    assert body["fixtures"][0]["home_team"] == "Team 00"
    assert body["deadline_at"] == iso(kickoff)
    assert any(w.startswith("KICKOFF_ASSUMED") for w in body["warnings"])
    current = c.get("/api/v1/rounds/current").json()
    assert current["round_id"] == ROUND and current["phase"] == "upcoming"
    assert current["confirmed"] is False and current["runs"] == []
    assert c.get(f"/api/v1/rounds/{ROUND}/predictions").status_code == 404  # nothing yet

    # --- odds typed into the round file for the first fixture drive its forecast
    spec = yaml.safe_load(path.read_text())
    spec["fixtures"][0]["odds"] = {"home": 1.8, "draw": 3.7, "away": 4.6, "over25": 1.9,
                                   "under25": 1.95, "available_at": iso(clock[0])}
    path.write_text(yaml.safe_dump(spec, sort_keys=False))

    # --- forecast: saved once; an identical request changes nothing
    gen = c.post("/api/v1/admin/predictions/generate", json={"round_id": ROUND}, headers=AUTH)
    assert gen.status_code == 200, gen.text
    first = gen.json()
    assert first["created"] is True and first["status"] == "PUBLISHED"
    assert any("ROUND_UNCONFIRMED" in w for w in first["warnings"])
    clock[0] += timedelta(hours=1)
    again = c.post("/api/v1/admin/predictions/generate", json={"round_id": ROUND},
                   headers=AUTH).json()
    assert again == first | {"created": False, "reason": "forecast unchanged"}
    run_id = first["run_id"]

    # --- an unconfirmed round cannot be locked; confirming it allows the lock
    lock_url = f"/api/v1/admin/runs/{run_id}/lock"
    refused = c.post(lock_url, headers=AUTH)
    assert refused.status_code == 409 and "unconfirmed" in refused.json()["detail"]
    confirmed = c.post(f"/api/v1/admin/rounds/{ROUND}/confirm",
                       json={"source": "Checked in the app (test)"}, headers=AUTH)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed"] is True
    assert confirmed.json()["confirmed_source"] == "Checked in the app (test)"
    assert yaml.safe_load(path.read_text())["confirmed"] is True
    before = c.get(f"/api/v1/rounds/{ROUND}/predictions").json()
    assert before["is_locked"] is False and before["prediction_run_id"] == run_id
    locked = c.post(lock_url, headers=AUTH)
    assert locked.status_code == 200 and locked.json()["status"] == "LOCKED"

    # --- the locked forecast reads back exactly as it was, and can't be replaced
    preds = c.get(f"/api/v1/rounds/{ROUND}/predictions").json()
    assert preds["is_locked"] is True and preds["locked_at"] is not None
    assert preds["fixtures"] == before["fixtures"] and preds["modes"] == before["modes"]
    regen = c.post("/api/v1/admin/predictions/generate", json={"round_id": ROUND},
                   headers=AUTH).json()
    assert regen["reason"] == "already locked" and regen["run_id"] == run_id
    assert _import(api).status_code == 409
    assert c.post(f"/api/v1/admin/rounds/{ROUND}/confirm", json={}, headers=AUTH
                  ).status_code == 409

    # --- shape of the forecast
    assert set(preds["modes"]) == {"points", "jackpot"}
    for mode in preds["modes"].values():
        assert len(mode["fixtures"]) == 6 and mode["points_scored"] is None
        joint = 1.0
        for pick in mode["fixtures"]:
            joint *= pick["exact_score_probability"]
        assert mode["joint_exact_probability"] == pytest.approx(joint)
        lines = mode["entry_summary"].splitlines()
        assert len(lines) == 7 and lines[-1].startswith("Golden Goal: minute ")
    picks = {m: {p["fixture_id"]: (p["predicted_home_goals"], p["predicted_away_goals"])
                 for p in v["fixtures"]} for m, v in preds["modes"].items()}
    assert preds["differing_fixture_ids"] == [
        f for f in picks["points"] if picks["points"][f] != picks["jackpot"][f]]
    assert preds["golden_goal"]["actual_minute"] is None
    assert sum(preds["golden_goal"]["distribution"].values()) == pytest.approx(1.0)
    basis = [f["forecast_basis"] for f in preds["fixtures"]]
    assert basis[0] == "market odds" and set(basis[1:]) == {"results model"}

    # --- one fixture: the grid and the explanation
    fid = preds["fixtures"][0]["fixture_id"]
    dist = c.get(f"/api/v1/fixtures/{fid}/distribution").json()
    grid = dist["grid"]
    assert sum(map(sum, grid)) + dist["tail_probability"] == pytest.approx(1.0)
    assert sum(dist["home_goals_distribution"]) == pytest.approx(sum(map(sum, grid)))
    side = range(dist["max_goals"] + 1)
    assert dist["outcome_probabilities"]["home"] == pytest.approx(
        sum(grid[h][a] for h in side for a in side if h > a))
    top = dist["top_scores"][0]
    assert top["probability"] == max(map(max, grid))
    assert c.get(f"/api/v1/fixtures/{fid}/distribution",
                 params={"run_id": run_id}).json() == dist
    explained = c.get(f"/api/v1/fixtures/{fid}/explanation").json()
    assert explained["odds"]["availability_basis"] == "declared"
    assert explained["odds"]["home"] == 1.8
    assert sum(explained["odds"]["implied"].values()) == pytest.approx(1.0)
    assert "entered in the round file" in explained["summary"][0]
    assert explained["market_expected_goals"] is not None
    assert len(explained["home_form"]) == 5 and set(explained["picks"]) == {"points", "jackpot"}
    assert all(f["kickoff_utc"] < preds["cutoff_at"] for f in explained["home_form"])
    other = c.get(f"/api/v1/fixtures/{preds['fixtures'][1]['fixture_id']}/explanation").json()
    assert other["odds"] is None and "No bookmaker odds" in other["summary"][0]
    assert other["warnings"][0]["code"] == "ENSEMBLE_TEAM_MODEL_ONLY"
    assert "backtests worse" in other["warnings"][0]["meaning"]

    # --- results arrive; settle with the official Golden Goal minute
    clock[0] = kickoff + timedelta(hours=1)
    assert c.get("/api/v1/rounds/current").json()["phase"] == "in_play"
    assert c.post("/api/v1/admin/predictions/generate", json={"round_id": ROUND},
                  headers=AUTH).status_code == 409  # the deadline has passed
    with session_scope(api["ctx"].engine) as s:
        for i, f in enumerate(preds["fixtures"]):
            s.add(FixtureResult(fixture_id=f["fixture_id"], source="test", home_goals=i % 3,
                                away_goals=1, result_available_at=kickoff + timedelta(hours=2)))
    clock[0] = kickoff + timedelta(hours=5)
    assert c.get("/api/v1/rounds/current").json()["phase"] == "awaiting_results"
    settled = c.post(f"/api/v1/admin/rounds/{ROUND}/settle", json={"golden_goal_minute": 7},
                     headers=AUTH)
    assert settled.status_code == 200, settled.text
    mine = next(x for x in settled.json()["settlements"] if x["run_id"] == run_id)
    assert mine["status"] == "SETTLED" and mine["metrics"]["golden_goal"]["actual_minute"] == 7

    after = c.get(f"/api/v1/rounds/{ROUND}/predictions").json()
    points = mine["metrics"]["points"]["points"]
    assert after["status"] == "SETTLED" and after["modes"]["points"]["points_scored"] == points
    assert after["modes"]["points"]["exact_hits"] == mine["metrics"]["points"]["exact_hits"]
    assert after["fixtures"][2]["result"] == {"home_goals": 2, "away_goals": 1}
    assert after["golden_goal"]["actual_minute"] == 7
    assert after["golden_goal"]["actual_source"] == "official"
    assert after["golden_goal"]["absolute_error"] == abs(after["golden_goal"]["minute"] - 7)
    # Only the results were added: the forecast itself did not change.
    assert [f | {"result": None} for f in after["fixtures"]] == preds["fixtures"]
    rnd = c.get(f"/api/v1/rounds/{ROUND}").json()
    assert rnd["phase"] == "settled" and rnd["locked"] is True
    assert c.get("/api/v1/rounds").json()[0]["official_run_id"] == run_id
    live = c.get("/api/v1/performance").json()["live"]
    assert live["totals"] == live["totals"] | {"rounds": 1, "locked_rounds": 1,
                                               "golden_goal_rounds": 1}
    assert live["rounds"][0]["record_type"] == "locked"
    assert live["rounds"][0]["points_mode_points"] == points
    assert live["rounds"][0]["golden_goal_actual"] == 7

    # --- every privileged change was logged
    audit = [json.loads(line) for line in
             (api["ctx"].reports_dir / "admin_audit.jsonl").read_text().splitlines()]
    assert [a["action"] for a in audit] == ["rounds.import", "predictions.generate",
                                            "rounds.confirm", "runs.lock", "rounds.settle"]
    assert all(TOKEN not in json.dumps(a) for a in audit)


def test_bad_requests_are_refused_clearly(api: dict[str, Any]) -> None:
    c: TestClient = api["client"]
    bad = api["client"].post("/api/v1/admin/rounds/import", headers=AUTH,
                             json={"text": "team-00 v Nowhere United"})
    assert bad.status_code == 422
    assert any("Nowhere United" in p for p in bad.json()["detail"]["problems"])
    assert not list(api["ctx"].rounds_dir.glob("*.yaml"))  # nothing was written
    # A round id becomes a file name, so anything path-like is refused before it is used.
    for sneaky in ("../../etc/passwd", "a/b", "..", "UPPER-case-id"):
        assert c.post("/api/v1/admin/predictions/generate", json={"round_id": sneaky},
                      headers=AUTH).status_code == 422
    assert c.post("/api/v1/admin/rounds/import", headers=AUTH,
                  json={"text": MATCHUPS, "surprise": 1}).status_code == 422
    assert c.get("/api/v1/rounds/NOT%20A%20ROUND").status_code == 422
    assert c.get("/api/v1/rounds/no-such-round").status_code == 404
    assert c.get("/api/v1/rounds/no-such-round/predictions").status_code == 404
    assert c.get("/api/v1/fixtures/SYN-20200101-a-b/distribution").status_code == 404
    assert c.get("/api/v1/fixtures/SYN-20200101-a-b/explanation",
                 params={"run_id": "not-a-run"}).status_code == 422
    assert c.post("/api/v1/admin/runs/run-0000000000000000/lock", headers=AUTH
                  ).status_code == 404
    assert c.post("/api/v1/admin/rounds/no-such-round/settle", json={}, headers=AUTH
                  ).status_code == 404
    assert c.post("/api/v1/admin/rounds/no-such-round/settle", headers=AUTH,
                  json={"golden_goal_minute": 95}).status_code == 422


def test_a_confirmed_import_can_be_locked_straight_away(api: dict[str, Any]) -> None:
    c: TestClient = api["client"]
    assert _import(api, confirmed=True).json()["confirmed"] is True
    run = c.post("/api/v1/admin/predictions/generate", json={"round_id": ROUND},
                 headers=AUTH).json()
    assert not any("ROUND_UNCONFIRMED" in w for w in run["warnings"])
    assert c.post(f"/api/v1/admin/runs/{run['run_id']}/lock", headers=AUTH).status_code == 200
    health = {x["name"]: x for x in c.get("/api/v1/health").json()["checks"]}
    assert health["current_round"] == {"name": "current_round", "status": "ok",
                                       "detail": f"{ROUND}: locked"}


def test_health_says_so_when_the_database_is_down(api: dict[str, Any]) -> None:
    Base.metadata.drop_all(api["ctx"].engine)
    down = api["client"].get("/api/v1/health")
    assert down.status_code == 503 and down.json()["status"] == "error"
    assert down.json()["checks"][0]["name"] == "database"


def test_warning_notes_explain_known_codes() -> None:
    notes = warning_notes(["E0-1-a-b:ENSEMBLE_TEAM_MODEL_ONLY", "TEAM_DATA_STALE:arsenal:31d",
                           "GOLDEN_GOAL_STAGGERED_KICKOFFS: minute 4 assumes ...",
                           "SOMETHING_NEW: details", "plain words"])
    assert [n.code for n in notes] == ["ENSEMBLE_TEAM_MODEL_ONLY", "TEAM_DATA_STALE",
                                       "GOLDEN_GOAL_STAGGERED_KICKOFFS", "SOMETHING_NEW",
                                       "NOTE"]
    assert "results-based model" in notes[0].meaning
    assert notes[3].meaning == "SOMETHING_NEW: details"  # unknown codes are shown as they are
