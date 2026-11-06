import pandas as pd

from super6.evaluation.rounds import replay_rounds, summarise_rounds
from super6.features.point_in_time import MatchHistory
from super6.ingestion.canonicalize import TeamRegistry
from super6.models import build_model
from super6.prediction.round_spec import RoundSpec
from super6.rules import CompetitionRules
from super6.synthetic import SyntheticTruth


def _round_from_history(history: MatchHistory, rules: CompetitionRules, offset: int,
                        illustrative: bool) -> RoundSpec:
    m = history.matches
    day = m["kickoff_utc"].iloc[-offset]
    six = m.loc[m["kickoff_utc"] == day].head(6)
    return RoundSpec.model_validate({
        "round_id": f"hist-{offset}", "deadline_utc": day.to_pydatetime(),
        "rules_version": rules.rules_version, "illustrative": illustrative,
        "fixtures": [{"home": r.home_team, "away": r.away_team, "competition": r.competition,
                      "kickoff_utc": r.kickoff_utc.to_pydatetime()} for r in six.itertuples()],
    })


def test_replay_scores_real_rounds_and_skips_illustrative(
    rules: CompetitionRules, league: tuple[MatchHistory, SyntheticTruth]
) -> None:
    history, _ = league
    # The synthetic league uses date-free fixture IDs; re-key them the way ingestion does.
    from super6.ingestion.adapters.football_data_couk import fixture_key
    from super6.timeutil import UK

    m = history.matches.copy()
    m["fixture_id"] = [fixture_key(c, k.tz_convert(UK).date(), h, a) for c, k, h, a in zip(
        m["competition"], m["kickoff_utc"], m["home_team"], m["away_team"], strict=True)]
    history = MatchHistory(m)
    reg = TeamRegistry()
    for t in sorted(set(m["home_team"])):
        reg.add_team(t, t)
    specs = [_round_from_history(history, rules, 6, False),
             _round_from_history(history, rules, 18, False),
             _round_from_history(history, rules, 30, True)]
    frame = replay_rounds(history, specs, reg, rules, lambda: build_model("poisson"), {"SYN"},
                          {"n_simulations": 500})
    by_id = frame.set_index("round_id")
    assert by_id.loc["hist-30", "status"] == "SKIPPED_ILLUSTRATIVE"
    assert set(by_id.loc[["hist-6", "hist-18"], "status"]) == {"SCORED"}
    assert set(by_id.loc[["hist-6", "hist-18"], "kind"]) == {"historical"}
    assert 0 <= by_id.loc["hist-6", "points_points"] <= 30
    summary = summarise_rounds(frame)
    assert summary["rounds"] == 2 and summary["blocked_or_skipped"] == 1
    # Forecasts were made before the deadline, so moving later results cannot change them.
    later = m.copy()
    later.loc[later["kickoff_utc"] > specs[1].deadline_utc, ["home_goals", "away_goals"]] = 0
    again = replay_rounds(MatchHistory(later), specs[1:2], reg, rules,
                          lambda: build_model("poisson"), {"SYN"}, {"n_simulations": 500})
    assert again.loc[0, "points_expected_points"] == by_id.loc["hist-18",
                                                                "points_expected_points"]
    assert pd.notna(by_id.loc["hist-6", "golden_goal_minute"])



def test_replay_scores_the_golden_goal_and_which_reading_the_answer_matches(
    rules: CompetitionRules, league: tuple[MatchHistory, SyntheticTruth]
) -> None:
    from super6.features.point_in_time import GOAL_COLUMNS
    from super6.ingestion.adapters.football_data_couk import fixture_key
    from super6.timeutil import UK

    history, _ = league
    m = history.matches.copy()
    m["fixture_id"] = [fixture_key(c, k.tz_convert(UK).date(), h, a) for c, k, h, a in zip(
        m["competition"], m["kickoff_utc"], m["home_team"], m["away_team"], strict=True)]
    reg = TeamRegistry()
    for t in sorted(set(m["home_team"])):
        reg.add_team(t, t)
    base = _round_from_history(MatchHistory(m), rules, 6, False)
    day = m["kickoff_utc"].iloc[-6]
    six = m.loc[m["kickoff_utc"] == day, "fixture_id"].head(6).tolist()  # base's fixtures
    # Stagger the kick-offs: the first game three hours earlier than the rest.
    early = six[0]
    m.loc[m["fixture_id"] == early, "kickoff_utc"] -= pd.Timedelta(hours=3)
    m.loc[m["fixture_id"].isin(six), ["home_goals", "away_goals"]] = [1, 0]
    m.loc[m["fixture_id"] == early, "result_available_at"] -= pd.Timedelta(hours=3)
    # The early game scores in minute 80 (first by the clock); a later one in minute 4.
    goals = pd.DataFrame(
        [(early, 2, 80, None, "home"), *[(f, 1, 4 if i == 1 else 30, None, "home")
                                         for i, f in enumerate(six) if f != early]],
        columns=GOAL_COLUMNS)
    hist = MatchHistory(m, history.odds, goals)
    fixtures = [{"home": r.home_team, "away": r.away_team, "competition": r.competition,
                 "kickoff_utc": r.kickoff_utc.to_pydatetime()}
                for r in m.loc[m["fixture_id"].isin(six)].itertuples()]
    spec = base.model_copy(update={
        "fixtures": [base.fixtures[0].model_validate(f) for f in fixtures],
        "deadline_utc": min(f["kickoff_utc"] for f in fixtures),
        "official_golden_goal_minute": 4})
    frame = replay_rounds(hist, [spec], reg, rules, lambda: build_model("poisson"), {"SYN"},
                          {"n_simulations": 500})
    row = frame.iloc[0]
    assert row["golden_goal_actual"] == 4 and row["golden_goal_actual_source"] == "official"
    assert row["golden_goal_abs_error"] == abs(row["golden_goal_minute"] - 4)
    assert bool(row["golden_goal_readings_differ"])
    assert bool(row["official_matches_earliest_match_minute"])
    assert not bool(row["official_matches_first_in_real_time"])
    summary = summarise_rounds(frame)
    assert summary["reading_evidence_rounds"] == 1
    assert summary["official_matches_earliest_match_minute"] == 1
    # Without an official answer, the earliest-minute answer comes from the goal minutes.
    plain = replay_rounds(hist, [spec.model_copy(update={"official_golden_goal_minute": None})],
                          reg, rules, lambda: build_model("poisson"), {"SYN"},
                          {"n_simulations": 500})
    assert plain.iloc[0]["golden_goal_actual"] == 4
    assert plain.iloc[0]["golden_goal_actual_source"] == "goal_minutes"

