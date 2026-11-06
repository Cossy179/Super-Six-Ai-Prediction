from datetime import date

from super6.ingestion.adapters.openfootball import parse_league_text, resolve_team
from super6.ingestion.canonicalize import TeamRegistry

TEXT = """\
= English Premier League 2023/24

Sat Dec 30 2023
  15:00   Arsenal FC             2-1 (2-0)  Nottingham Forest FC
                (Eddie NKETIAH 26', Bukayo SAKA 45+2';
                 Taiwo AWONIYI 90+5')
  17:30   Manchester United FC   1-0 (0-0)  Wolverhampton Wanderers FC
                (Raphael VARANE 76')
Mon Jan 1
  20:00   Wolverhampton Wanderers FC  0-2 (0-1)  Brighton & Hove Albion FC
                (Kaoru MITOMA 15', Solly MARCH 56')
  20:00   Brighton & Hove Albion FC   1-1  Arsenal FC
                (Lewis DUNK 30' (og); Ben WHITE 70')
  20:00   Arsenal FC   0-0  Manchester United FC
  20:00   Nottingham Forest FC   3-0  Wolverhampton Wanderers FC
                (Chris WOOD 10', 20')
  20:00   Manchester United FC   2-0  Brighton & Hove Albion FC
"""


def test_parses_goals_sides_stoppage_and_year_rollover() -> None:
    parsed = parse_league_text(TEXT, 2023)
    by_teams = {(m.home, m.away): m for m in parsed.matches}
    first = by_teams[("Arsenal FC", "Nottingham Forest FC")]
    assert first.match_date == date(2023, 12, 30)
    assert [(g.side, g.minute, g.stoppage, g.period) for g in first.goals] == [
        ("home", 26, None, 1), ("home", 45, 2, 1), ("away", 90, 5, 2)]
    # The season crosses New Year: "Mon Jan 1" without a year is 2024.
    away_only = by_teams[("Wolverhampton Wanderers FC", "Brighton & Hove Albion FC")]
    assert away_only.match_date == date(2024, 1, 1)
    # Only one side scored and the file has no ";": goals go to the side that scored.
    assert [g.side for g in away_only.goals] == ["away", "away"]
    home_only = by_teams[("Manchester United FC", "Wolverhampton Wanderers FC")]
    assert [g.side for g in home_only.goals] == ["home"]
    # Own goals are listed under the team they count for.
    og = by_teams[("Brighton & Hove Albion FC", "Arsenal FC")]
    assert [(g.side, g.minute) for g in og.goals] == [("home", 30), ("away", 70)]
    # A goalless match is a match with no goals, not a missing goal list.
    assert by_teams[("Arsenal FC", "Manchester United FC")].goals == []


def test_rejects_mismatches_and_counts_missing_goal_lists() -> None:
    parsed = parse_league_text(TEXT, 2023)
    assert len(parsed.problems) == 1 and "goal list has 2-0 but the score is 3-0" in (
        parsed.problems[0])
    assert parsed.without_goal_times == 1  # Man United 2-0 Brighton has no goal list
    assert len(parsed.matches) == 5


def test_resolves_names_with_fc_suffix() -> None:
    reg = TeamRegistry.from_config()
    assert resolve_team(reg, "Nottingham Forest FC") == "nottm-forest"
    assert resolve_team(reg, "Brighton & Hove Albion FC") == "brighton"
    assert resolve_team(reg, "Wolverhampton Wanderers FC") == "wolves"
