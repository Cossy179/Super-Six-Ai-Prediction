from pathlib import Path

from sqlalchemy import Engine, func, select

from super6.ingestion.pipeline import ingest_raw_dir
from super6.storage.db import session_scope
from super6.storage.models import DataQualityIssue, Fixture, FixtureResult, MarketSnapshot, Team
from super6.storage.repositories import load_history

HEADER = ("Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,AvgH,AvgD,AvgA,Avg>2.5,Avg<2.5,"
          "AvgCH,AvgCD,AvgCA\n")
ROWS = [
    "E0,12/08/2023,15:00,Arsenal,Nott'm Forest,2,1,H,1.2,7.0,15.0,1.5,2.6,1.18,7.5,16\n",
    "E0,12/08/2023,15:00,Man United,Wolves,1,0,H,1.4,5.0,7.5,1.6,2.3,1.38,5.2,8\n",
    "E0,19/08/2023,17:30,Wolves,Brighton,1,4,A,3.1,3.6,2.2,1.6,2.3,3.2,3.7,2.1\n",
]


def _write(raw_dir: Path, rows: list[str]) -> Path:
    path = raw_dir / "football_data_couk" / "2324" / "E0.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + "".join(rows), encoding="utf-8")
    return path


def _counts(engine: Engine) -> tuple[int, int, int]:
    with session_scope(engine) as s:
        return (s.scalar(select(func.count(Fixture.id))) or 0,
                s.scalar(select(func.count(FixtureResult.id))) or 0,
                s.scalar(select(func.count(MarketSnapshot.id))) or 0)


def test_ingest_is_idempotent_and_keeps_original_results(engine: Engine, tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _write(raw, ROWS)
    with session_scope(engine) as s:
        first = ingest_raw_dir(s, raw, log=lambda _: None)
    assert [st.fixtures_new for st in first] == [3]
    assert _counts(engine) == (3, 3, 6)
    with session_scope(engine) as s:
        again = ingest_raw_dir(s, raw, log=lambda _: None)
    assert again[0].skipped and _counts(engine) == (3, 3, 6)

    # A revised file: one changed score, one new match.
    revised = [ROWS[0].replace(",2,1,H,", ",3,1,H,"), *ROWS[1:],
               "E0,26/08/2023,15:00,Brighton,Arsenal,0,0,D,2.5,3.4,2.9,1.8,2.0,2.4,3.4,3.0\n"]
    _write(raw, revised)
    with session_scope(engine) as s:
        third = ingest_raw_dir(s, raw, log=lambda _: None)
    assert third[0].fixtures_new == 1 and third[0].result_conflicts == 1
    with session_scope(engine) as s:
        conflict = s.scalar(select(DataQualityIssue).where(
            DataQualityIssue.code == "RESULT_CONFLICT"))
        assert conflict is not None and "original kept" in conflict.detail
        kept = s.scalar(select(FixtureResult).where(
            FixtureResult.fixture_id == "E0-20230812-arsenal-nottm-forest"))
        assert kept is not None and (kept.home_goals, kept.away_goals) == (2, 1)
        assert s.get(Team, "man-united").canonical_name == "Manchester United"  # type: ignore[union-attr]
        history = load_history(s)
    assert len(history.matches) == 4
    assert set(history.odds["odds_type"]) == {"prematch", "closing"}
    assert history.matches["kickoff_utc"].dt.tz is not None
