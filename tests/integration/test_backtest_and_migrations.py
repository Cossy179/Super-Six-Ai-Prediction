import os
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import DBAPIError

from super6.cli import upgrade_database
from super6.evaluation.replay import synthetic_rounds, walk_forward
from super6.evaluation.reports import build_report, write_report
from super6.features.point_in_time import MatchHistory
from super6.models import build_model
from super6.rules import CompetitionRules
from super6.storage.db import make_engine, session_scope
from super6.storage.models import (
    Base,
    Competition,
    Fixture,
    ModelRegistryEntry,
    PredictionRun,
    Super6Round,
    Team,
)
from super6.synthetic import SyntheticTruth


def test_backtest_report_end_to_end(tmp_path: Path, rules: CompetitionRules,
                                    league: tuple[MatchHistory, SyntheticTruth]) -> None:
    history, _ = league
    m = history.matches
    start, end = m["kickoff_utc"].iloc[-90], m["kickoff_utc"].iloc[-1] + pd.Timedelta(days=1)
    models = {n: build_model(n) for n in ("dixon_coles", "league_average", "market_poisson")}
    rows = walk_forward(history, models, rules, start, end)
    assert set(rows["model"]) == set(models)
    per_model = rows.groupby("model")["fixture_id"].apply(frozenset)
    assert len(set(per_model)) == 1  # every model forecast the same fixtures
    rounds = synthetic_rounds(rows, ["SYN"], weekday=5, local_kickoff="15:00",
                              fixtures_per_round=6, seed=1)
    assert not rounds.empty and rounds["synthetic"].all()
    report = build_report(rows, rounds, reference="dixon_coles", resamples=200, meta={
        "label": "synthetic", "start": str(start), "end": str(end), "competitions": ["SYN"],
        "refit_every_days": 1, "rules_version": rules.rules_version, "rules_verified": False,
        "code_version": "test", "synthetic_competitions": ["SYN"]})
    paths = write_report(report, rows, rounds, tmp_path / "bt")
    text = paths["markdown"].read_text()
    assert "NOT historical Super 6 rounds" in text
    assert "Paired comparison" in text
    # With synthetic odds generated from the true model, the market should beat the
    # no-information league-average baseline on log loss.
    overall = {r["model"]: r for r in report.summary["overall"]}
    assert overall["market_poisson"]["exact_log_loss"] < overall["league_average"][
        "exact_log_loss"]


def _check_migrations(url: str) -> None:
    upgrade_database(url)
    engine = make_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables) <= tables
    _check_lock_triggers(engine)
    engine.dispose()


def _check_lock_triggers(engine: Engine) -> None:
    """Migrated databases refuse raw SQL that would alter a locked run."""
    now = datetime(2026, 11, 7, 12, tzinfo=UTC)
    with session_scope(engine) as s:
        s.add(Competition(id="T0", name="T", country="T", pool="t", tier=1))
        s.add_all([Team(id="t-a", canonical_name="A"), Team(id="t-b", canonical_name="B")])
        s.flush()  # no relationship() on these models, so flush parents first
        s.add(Fixture(id="T0-20261107-t-a-t-b", competition_id="T0", season="2026-27",
                      home_team_id="t-a", away_team_id="t-b", kickoff_utc=now,
                      kickoff_time_known=True, neutral=False, status="SCHEDULED"))
        s.add(Super6Round(id="trigger-test", deadline_utc=now, rules_version="r",
                          status="OPEN", illustrative=True, source_note=None, spec={}))
        s.add(ModelRegistryEntry(model_name="m", code_version="v", config_hash="h", config={},
                                 training_cutoff=now, training_fingerprint="f", summary={},
                                 artifact_uri=None))
        s.flush()
        model_id = s.scalar(select(ModelRegistryEntry.id))
        s.add(PredictionRun(id="run-trigger", round_id="trigger-test", model_registry_id=model_id,
                            cutoff_at=now, status="LOCKED", code_version="v", rules_version="r",
                            input_fingerprint="f", config={}, warnings=[], block_reasons=[],
                            published_at=None, locked_at=now, settled_at=None))
    for sql in (
        "INSERT INTO recommendations (run_id, mode, fixture_id, home_goals, away_goals, "
        "exact_probability, result_probability, expected_points, alternatives) VALUES "
        "('run-trigger', 'points', 'T0-20261107-t-a-t-b', 1, 0, 0.1, 0.4, 1.1, '[]')",
        "UPDATE prediction_runs SET cutoff_at = created_at WHERE id = 'run-trigger'",
        "UPDATE prediction_runs SET status = 'PUBLISHED' WHERE id = 'run-trigger'",
        "DELETE FROM prediction_runs WHERE id = 'run-trigger'",
    ):
        with pytest.raises(DBAPIError), engine.begin() as conn:
            conn.execute(text(sql))
    with engine.begin() as conn:  # the one permitted change
        conn.execute(text("UPDATE prediction_runs SET status = 'SETTLED', settled_at = "
                          "created_at WHERE id = 'run-trigger'"))


def test_alembic_migrations_match_models_sqlite(tmp_path: Path) -> None:
    _check_migrations(f"sqlite:///{tmp_path / 'migrated.db'}")


@pytest.mark.postgres
@pytest.mark.skipif(not os.environ.get("SUPER6_TEST_POSTGRES_URL"),
                    reason="set SUPER6_TEST_POSTGRES_URL to a disposable database")
def test_alembic_migrations_postgres() -> None:
    _check_migrations(os.environ["SUPER6_TEST_POSTGRES_URL"])
