from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from super6.features.point_in_time import MatchHistory
from super6.rules import CompetitionRules, load_rules
from super6.storage.db import create_schema, make_engine
from super6.synthetic import SyntheticTruth, synthetic_history

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _repo_configs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPER6_HOME", str(REPO))


@pytest.fixture(scope="session")
def rules() -> CompetitionRules:
    return load_rules(REPO / "configs" / "competition_rules.yaml")


@pytest.fixture(scope="session")
def league() -> tuple[MatchHistory, SyntheticTruth]:
    return synthetic_history(n_teams=12, seasons=3, seed=11, with_odds=True)


@pytest.fixture()
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    create_schema(eng)
    yield eng
    eng.dispose()
