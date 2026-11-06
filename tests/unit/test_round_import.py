from datetime import UTC, date, datetime

import pandas as pd
import pytest
import yaml

from super6.domain import FixtureStatus
from super6.features.point_in_time import MatchHistory
from super6.ingestion.canonicalize import TeamRegistry
from super6.ingestion.round_import import (
    RoundImportError,
    import_round,
    next_saturday,
    parse_fixture_text,
)
from super6.prediction.round_spec import RoundSpec, resolve_round
from super6.rules import CompetitionRules

SAT = date(2026, 11, 7)


def _history() -> MatchHistory:
    rows = []
    teams = {"E0": ["fulham", "newcastle", "man-united", "aston-villa"],
             "E1": ["wrexham", "norwich"]}
    k = 0
    for comp, ts in teams.items():
        for i, h in enumerate(ts):
            for a in ts[i + 1:]:
                ko = pd.Timestamp("2026-10-03 14:00", tz="UTC") + pd.Timedelta(days=k)
                k += 1
                rows.append({"fixture_id": f"{comp}-{k}", "competition": comp,
                             "season": "2026-27", "kickoff_utc": ko, "kickoff_time_known": True,
                             "home_team": h, "away_team": a, "status": "FINISHED",
                             "home_goals": 1, "away_goals": 0,
                             "result_available_at": ko + pd.Timedelta(hours=2)})
    # The provider already lists one of this weekend's games, at 17:30 UK (GMT).
    rows.append({"fixture_id": "E0-20261107-man-united-aston-villa", "competition": "E0",
                 "season": "2026-27", "kickoff_utc": pd.Timestamp("2026-11-07 17:30", tz="UTC"),
                 "kickoff_time_known": True, "home_team": "man-united",
                 "away_team": "aston-villa", "status": FixtureStatus.SCHEDULED.value,
                 "home_goals": None, "away_goals": None, "result_available_at": None})
    return MatchHistory(pd.DataFrame(rows))


def _registry() -> TeamRegistry:
    reg = TeamRegistry.from_config()
    return reg


def test_parses_common_formats() -> None:
    pairs = parse_fixture_text(
        "1. Fulham v Newcastle\n2) Man Utd vs Aston Villa 17:30; Wrexham - Norwich (E1), "
        "Leeds versus Everton 12.30")
    assert [(p.home, p.away) for p in pairs] == [
        ("Fulham", "Newcastle"), ("Man Utd", "Aston Villa"), ("Wrexham", "Norwich"),
        ("Leeds", "Everton")]
    assert pairs[1].local_time == (17, 30) and pairs[3].local_time == (12, 30)
    assert pairs[2].competition == "E1"
    with pytest.raises(RoundImportError):
        parse_fixture_text("Fulham Newcastle")


def test_fills_from_provider_list_and_infers_the_rest(rules: CompetitionRules) -> None:
    pairs = parse_fixture_text("Fulham v Newcastle, Man Utd v Aston Villa 15:00, "
                               "Wrexham v Norwich")
    imp = import_round(pairs, _registry(), _history(), SAT, round_number=16,
                       known_competitions={"E0", "E1"})
    assert imp.round_id == "super6-2026-27-round-16"
    by_home = {f.home_team: f for f in imp.fixtures}
    mu = by_home["man-united"]
    assert mu.source == "provider_fixture_list"
    assert mu.kickoff_utc == datetime(2026, 11, 7, 17, 30, tzinfo=UTC)
    assert any("provider lists 17:30" in w for w in imp.warnings)  # user said 15:00
    assert by_home["fulham"].source == "assumed" and by_home["fulham"].competition == "E0"
    assert by_home["wrexham"].competition == "E1"
    assert sum(w.startswith("KICKOFF_ASSUMED") for w in imp.warnings) == 2
    # Deadline is the first kick-off: the two inferred games at 15:00 UK (= UTC in Nov).
    assert imp.deadline_utc == datetime(2026, 11, 7, 15, 0, tzinfo=UTC)
    spec = RoundSpec.model_validate(yaml.safe_load(imp.to_yaml(rules.rules_version, "test")))
    assert spec.illustrative is False and len(spec.fixtures) == 3
    resolved = resolve_round(spec, _registry(), rules, {"E0", "E1"})
    assert [b for b in resolved.block_reasons if not b.startswith("WRONG_FIXTURE_COUNT")] == []


def test_reports_unknown_teams_and_unclear_competitions() -> None:
    with pytest.raises(RoundImportError) as err:
        import_round(parse_fixture_text("Fulhm v Newcastle, Fulham v Wrexham"), _registry(),
                     _history(), SAT, known_competitions={"E0", "E1"})
    problems = err.value.problems
    assert any("Fulhm" in p and "fulham" in p for p in problems)  # suggestion offered
    assert any("can't tell the competition" in p for p in problems)
    ok = import_round(parse_fixture_text("Fulham v Wrexham (E0)"), _registry(), _history(),
                      SAT, known_competitions={"E0", "E1"})
    assert ok.fixtures[0].competition == "E0"


def test_parses_days_and_12_hour_times() -> None:
    pairs = parse_fixture_text(
        "Arsenal vs Hull, Saturday 3pm, Crystal Palace vs Liverpool, Sunday 2pm\n"
        "Man Utd v Aston Villa Sun 4.30pm; Nottingham Forest vs Man City, Saturday 5.30pm")
    got = [(p.home, p.away, p.weekday, p.local_time) for p in pairs]
    assert got == [("Arsenal", "Hull", 5, (15, 0)), ("Crystal Palace", "Liverpool", 6, (14, 0)),
                   ("Man Utd", "Aston Villa", 6, (16, 30)),
                   ("Nottingham Forest", "Man City", 5, (17, 30))]


def test_sunday_fixtures_land_on_sunday() -> None:
    imp = import_round(parse_fixture_text("Fulham v Newcastle, Sunday 2pm"), _registry(),
                       _history(), SAT, known_competitions={"E0", "E1"})
    assert imp.fixtures[0].kickoff_utc == datetime(2026, 11, 8, 14, 0, tzinfo=UTC)
    assert not any(w.startswith("KICKOFF_ASSUMED") for w in imp.warnings)


def test_next_saturday() -> None:
    assert next_saturday(date(2026, 11, 6)) == SAT
    assert next_saturday(SAT) == SAT


def test_round_file_fixtures_are_not_mistaken_for_provider_listings() -> None:
    h = _history()
    m = h.matches.copy()
    m["fixture_source"] = "football_data_couk"
    m.loc[m["fixture_id"] == "E0-20261107-man-united-aston-villa", "fixture_source"] = \
        "round_file"
    imp = import_round(parse_fixture_text("Man Utd v Aston Villa, Sunday 4.30pm"), _registry(),
                       MatchHistory(m), SAT, known_competitions={"E0", "E1"})
    assert imp.fixtures[0].source == "text"
    assert imp.fixtures[0].kickoff_utc == datetime(2026, 11, 8, 16, 30, tzinfo=UTC)


def _spec_payload(rules: CompetitionRules, **extra: object) -> dict[str, object]:
    return {"round_id": "test-round", "deadline_utc": "2026-11-07T15:00:00Z",
            "rules_version": rules.rules_version,
            "fixtures": [{"home": "fulham", "away": "newcastle", "competition": "E0",
                          "kickoff_utc": "2026-11-07T15:00:00Z"}]} | extra


def test_rounds_are_unconfirmed_unless_a_confirmation_is_recorded(
        rules: CompetitionRules) -> None:
    # A file that says nothing about confirmation is unconfirmed (fail-safe).
    assert RoundSpec.model_validate(_spec_payload(rules)).confirmed is False
    # A confirmation must say when and how the fixtures were checked.
    with pytest.raises(ValueError, match="confirmed_at and confirmed_source"):
        RoundSpec.model_validate(_spec_payload(rules, confirmed=True))
    with pytest.raises(ValueError, match="confirmed_at and confirmed_source"):
        RoundSpec.model_validate(_spec_payload(rules, confirmed=True,
                                               confirmed_at="2026-11-06T12:00:00Z",
                                               confirmed_source="  "))
    ok = RoundSpec.model_validate(_spec_payload(
        rules, confirmed=True, confirmed_at="2026-11-06T12:00:00Z",
        confirmed_source="Checked against the Super 6 app"))
    assert ok.confirmed and ok.confirmed_at == datetime(2026, 11, 6, 12, tzinfo=UTC)


def test_imported_rounds_record_their_confirmation(rules: CompetitionRules) -> None:
    pairs = parse_fixture_text("Fulham v Newcastle")
    imp = import_round(pairs, _registry(), _history(), SAT, known_competitions={"E0", "E1"})
    fetched = RoundSpec.model_validate(yaml.safe_load(imp.to_yaml(rules.rules_version, "x")))
    assert fetched.confirmed is False and fetched.confirmed_at is None
    when = datetime(2026, 11, 6, 9, 30, tzinfo=UTC)
    pasted = RoundSpec.model_validate(yaml.safe_load(
        imp.to_yaml(rules.rules_version, "x", (when, "pasted from the app"))))
    assert pasted.confirmed and pasted.confirmed_at == when
    assert pasted.confirmed_source == "pasted from the app"
