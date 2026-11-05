from datetime import UTC, datetime
from pathlib import Path

import pytest

from super6.ingestion.adapters import football_data_couk as fdc
from super6.ingestion.canonicalize import TeamRegistry, UnknownTeamError, normalise_name, slugify

RETRIEVED = datetime(2024, 6, 1, tzinfo=UTC)


def _raw(text: str, uri: str = "https://example.test/E0.csv") -> fdc.RawFile:
    content = text.encode("utf-8-sig")
    return fdc.RawFile(fdc.SOURCE, uri, Path("x.csv"), RETRIEVED, fdc._sha256(content), content)


HEADER = ("Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,AvgH,AvgD,AvgA,Avg>2.5,Avg<2.5,"
          "AvgCH,AvgCD,AvgCA\n")


def test_names_and_slugs() -> None:
    assert slugify("Nott'm Forest") == "nottm-forest"
    assert normalise_name("Brighton & Hove Albion") == "brighton and hove albion"
    assert normalise_name("  Sheffield   Weds ") == "sheffield weds"


def test_registry_aliases_and_strict_resolution() -> None:
    reg = TeamRegistry()
    reg.add_team("man-united", "Manchester United", ["Man United", "Man Utd"])
    assert reg.register_source_name("Man United") == "man-united"
    assert reg.resolve("manchester united") == "man-united"
    with pytest.raises(UnknownTeamError) as err:
        reg.resolve("Manchester Untied")
    assert "man-united" in err.value.suggestions
    with pytest.raises(ValueError):
        reg.add_team("man-city", "Manchester City", ["Man Utd"])  # alias conflict


def test_config_aliases_cover_known_provider_renames() -> None:
    reg = TeamRegistry.from_config()
    assert reg.register_source_name("Sheffield Utd") == "sheffield-united"
    assert reg.register_source_name("Oxford Utd") == "oxford"
    assert reg.resolve("Nottingham Forest") == "nottm-forest"


def test_parse_season_file_with_odds_and_timezones() -> None:
    text = HEADER + (
        "E0,12/08/2023,15:00,Arsenal,Nott'm Forest,2,1,H,1.2,7.0,15.0,1.5,2.6,1.18,7.5,16\n"
        "E0,26/12/23,12:30,Chelsea,Arsenal,0,0,D,2.5,3.4,2.9,,,2.4,3.4,3.0\n"
    )
    parsed = fdc.parse_csv(_raw(text), TeamRegistry(), competition="E0", season_label="2023-24")
    assert parsed.report is not None and parsed.report.rows_accepted == 2
    first, second = parsed.matches
    assert first["kickoff_utc"] == datetime(2023, 8, 12, 14, 0, tzinfo=UTC)  # BST → UTC
    assert second["kickoff_utc"] == datetime(2023, 12, 26, 12, 30, tzinfo=UTC)  # GMT
    assert first["fixture_id"] == "E0-20230812-arsenal-nottm-forest"
    assert first["result_available_at"] > first["kickoff_utc"]
    pre = [o for o in parsed.odds if o["odds_type"] == "prematch"]
    close = [o for o in parsed.odds if o["odds_type"] == "closing"]
    assert len(pre) == 2 and len(close) == 2
    assert pre[0]["over25"] == 1.5 and pre[1]["over25"] is None
    # Saturday game: odds collected Friday 18:00 UK (17:00 UTC in August).
    assert pre[0]["available_at"] == datetime(2023, 8, 11, 17, 0, tzinfo=UTC)
    assert all(c["available_at"] == m["kickoff_utc"] for c, m in zip(close, parsed.matches,
                                                                      strict=True))


def test_missing_time_column_is_conservative() -> None:
    text = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nE0,13/08/2016,Hull,Leicester,2,1,H\n"
    parsed = fdc.parse_csv(_raw(text), TeamRegistry(), competition="E0", season_label="2016-17")
    m = parsed.matches[0]
    assert m["kickoff_time_known"] is False
    # Result only counted as public at the end of the local match day.
    assert m["result_available_at"] == datetime(2016, 8, 13, 22, 59, 59, tzinfo=UTC)


def test_validation_quarantines_bad_rows() -> None:
    text = HEADER + (
        "E0,12/08/2023,15:00,Arsenal,Chelsea,2,1,A,,,,,,,,\n"  # result code mismatch
        "E0,12/08/2023,15:00,Spurs,Spurs,1,1,D,,,,,,,,\n"  # same team
        "E0,12/08/2023,15:00,Leeds,Hull,-1,0,A,,,,,,,,\n"  # negative score
        "E0,12/08/2019,15:00,Leeds,Wolves,1,0,H,,,,,,,,\n"  # wrong season
        ",,,,,,,,,,,,,,,\n"  # blank trailing row
        "E0,19/08/2023,15:00,Hull,Leeds,1,0,H,,,,,,,,\n"
        "E0,19/08/2023,15:00,Hull,Leeds,1,0,H,,,,,,,,\n"  # duplicate
    )
    parsed = fdc.parse_csv(_raw(text), TeamRegistry(), competition="E0", season_label="2023-24")
    codes = sorted(i.code for i in parsed.report.issues) if parsed.report else []
    assert codes == ["DATE_OUTSIDE_SEASON", "DUPLICATE_FIXTURE", "NEGATIVE_SCORE",
                     "RESULT_CODE_MISMATCH", "SAME_TEAM"]
    assert len(parsed.matches) == 1


def test_possible_rename_is_flagged_not_merged() -> None:
    reg = TeamRegistry()
    reg.add_team("sheffield-united", "Sheffield United")
    text = HEADER + (
        "E1,12/08/2023,15:00,Sheffield United,Hull,2,1,H,,,,,,,,\n"
        "E1,19/08/2023,15:00,Sheffield Utd,Leeds,1,1,D,,,,,,,,\n"
    )
    parsed = fdc.parse_csv(_raw(text), reg, competition="E1", season_label="2023-24")
    flags = [i for i in parsed.report.issues if i.code == "POSSIBLE_DUPLICATE_TEAM"]
    assert len(flags) == 1 and "Sheffield Utd" in flags[0].detail
    assert {m["home_team"] for m in parsed.matches} == {"sheffield-united", "sheffield-utd"}


@pytest.mark.parametrize(
    ("kickoff", "expected"),
    [
        (datetime(2024, 3, 16, 15, 0, tzinfo=UTC), datetime(2024, 3, 15, 18, 0, tzinfo=UTC)),
        (datetime(2024, 3, 17, 16, 30, tzinfo=UTC), datetime(2024, 3, 15, 18, 0, tzinfo=UTC)),
        (datetime(2024, 3, 12, 19, 45, tzinfo=UTC), datetime(2024, 3, 12, 18, 0, tzinfo=UTC)),
        (datetime(2024, 3, 13, 19, 45, tzinfo=UTC), datetime(2024, 3, 12, 18, 0, tzinfo=UTC)),
        (datetime(2024, 3, 15, 17, 30, tzinfo=UTC), datetime(2024, 3, 15, 16, 30, tzinfo=UTC)),
    ],
)
def test_prematch_odds_availability_rule(kickoff: datetime, expected: datetime) -> None:
    assert fdc.prematch_odds_available_at(kickoff) == expected


def test_season_codes() -> None:
    assert fdc.season_code("2023-24") == "2324"
    assert fdc.season_label_from_code("9900") == "1999-00"
    assert fdc.seasons_between("2022-23", "2024-25") == ["2022-23", "2023-24", "2024-25"]
