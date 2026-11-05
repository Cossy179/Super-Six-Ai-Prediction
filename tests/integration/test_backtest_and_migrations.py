import os
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import inspect

from super6.cli import upgrade_database
from super6.evaluation.replay import synthetic_rounds, walk_forward
from super6.evaluation.reports import build_report, write_report
from super6.features.point_in_time import MatchHistory
from super6.models import build_model
from super6.rules import CompetitionRules
from super6.storage.db import make_engine
from super6.storage.models import Base
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
    engine.dispose()
    assert set(Base.metadata.tables) <= tables


def test_alembic_migrations_match_models_sqlite(tmp_path: Path) -> None:
    _check_migrations(f"sqlite:///{tmp_path / 'migrated.db'}")


@pytest.mark.postgres
@pytest.mark.skipif(not os.environ.get("SUPER6_TEST_POSTGRES_URL"),
                    reason="set SUPER6_TEST_POSTGRES_URL to a disposable database")
def test_alembic_migrations_postgres() -> None:
    _check_migrations(os.environ["SUPER6_TEST_POSTGRES_URL"])
