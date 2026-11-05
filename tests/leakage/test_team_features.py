"""Point-in-time guarantees for the ML feature builder."""

import math

import pandas as pd
import pytest

from super6.features.point_in_time import LeakageError, MatchHistory
from super6.features.team_features import FeatureBuilder, FeatureConfig, snapshot
from super6.synthetic import SyntheticTruth

TIERS = {"SYN": 1}


def test_training_rows_only_use_earlier_results(
    league: tuple[MatchHistory, SyntheticTruth]
) -> None:
    history, _ = league
    results = history.view(history.matches["kickoff_utc"].max() + pd.Timedelta(days=1)).results
    full, _ = FeatureBuilder(TIERS).training_table(results)
    # Rebuild from a truncated history: rows before the cut must be identical, because a
    # row may only depend on results published before its own kick-off.
    cut = results["kickoff_utc"].iloc[len(results) // 2]
    early = results.loc[results["kickoff_utc"] <= cut]
    partial, _ = FeatureBuilder(TIERS).training_table(early)
    pd.testing.assert_frame_equal(full.loc[partial.index], partial)
    # And a match's own result never shapes its own features.
    changed = results.copy()
    target = changed.index[len(changed) // 2]
    changed.loc[target, ["home_goals", "away_goals"]] = [9, 0]
    again, _ = FeatureBuilder(TIERS).training_table(changed)
    fid = changed.loc[target, "fixture_id"]
    pd.testing.assert_series_equal(full.loc[fid], again.loc[fid])


def test_same_day_results_wait_for_publication() -> None:
    ko = pd.Timestamp("2024-03-02 12:30", tz="UTC")
    results = pd.DataFrame([{
        "fixture_id": "early", "competition": "SYN", "home_team": "a", "away_team": "b",
        "home_goals": 3, "away_goals": 0, "kickoff_utc": ko,
        "result_available_at": ko + pd.Timedelta(minutes=135), "neutral": False,
    }])
    state = FeatureBuilder(TIERS).new_state()
    FeatureBuilder.advance(state, results, ko + pd.Timedelta(hours=2))  # before publication
    assert "a" not in state.teams
    FeatureBuilder.advance(state, results, ko + pd.Timedelta(hours=3))
    assert state.teams["a"].elo > state.teams["b"].elo


def test_incremental_advance_matches_full_rebuild(
    league: tuple[MatchHistory, SyntheticTruth]
) -> None:
    history, _ = league
    results = history.view(history.matches["kickoff_utc"].max() + pd.Timedelta(days=1)).results
    builder = FeatureBuilder(TIERS)
    mid = results["result_available_at"].iloc[len(results) // 2]
    _, state = builder.training_table(results.loc[results["result_available_at"] <= mid])
    FeatureBuilder.advance(state, results, results["result_available_at"].max())
    _, fresh = builder.training_table(results)
    probe = ("team-00", "team-05", "SYN", results["kickoff_utc"].max() + pd.Timedelta(days=3))
    a, b = state.features(*probe), fresh.features(*probe)
    assert a.keys() == b.keys()
    for k in a:
        if isinstance(a[k], float) and math.isnan(a[k]):
            assert math.isnan(b[k])
        else:
            assert a[k] == pytest.approx(b[k]), k


def test_elo_ewm_schedule_and_tier_features() -> None:
    t0 = pd.Timestamp("2024-08-10 14:00", tz="UTC")
    rows = []
    for i, (h, a, hg, ag, comp) in enumerate([
        ("a", "b", 2, 0, "L2"), ("b", "a", 1, 1, "L2"), ("a", "c", 4, 1, "L1"),
    ]):
        ko = t0 + pd.Timedelta(days=7 * i)
        rows.append({"fixture_id": f"m{i}", "competition": comp, "home_team": h,
                     "away_team": a, "home_goals": hg, "away_goals": ag, "kickoff_utc": ko,
                     "result_available_at": ko + pd.Timedelta(hours=2), "neutral": False,
                     "home_sot": 5.0, "away_sot": 2.0})
    results = pd.DataFrame(rows)
    builder = FeatureBuilder({"L1": 1, "L2": 2}, FeatureConfig(spans=(2,)))
    table, state = builder.training_table(results)
    # Zero-sum Elo; teams new to tier 2 start one tier gap (100) below tier 1.
    total = sum(state.teams[t].elo for t in "abc")
    assert total == pytest.approx(1400 + 1400 + 1500)
    first = table.loc["m0"]
    assert first["home_elo"] == first["away_elo"] == 1400
    assert math.isnan(first["home_goals_for_2"]) and first["home_matches"] == 0
    third = table.loc["m2"]  # a promoted from L2 to L1
    assert third["home_tier_change"] == -1
    assert third["home_rest_days"] == pytest.approx(7.0)
    assert third["home_matches_14d"] == 2
    # Bias-corrected EWMA, span 2 (alpha = 2/3): team a scored 2 then 1, and had 5 then 2
    # shots on target, so the means are (2/3·2 + 1)/(4/3) and (2/3·5 + 2)/(4/3).
    assert third["home_goals_for_2"] == pytest.approx(1.25)
    assert third["home_sot_for_2"] == pytest.approx(2.75)
    feats = state.features("a", "zzz", "L1", t0 + pd.Timedelta(days=30))
    assert feats["away_matches"] == 0 and feats["away_elo"] == 1500


def test_odds_as_of_cannot_look_past_the_cutoff(
    league: tuple[MatchHistory, SyntheticTruth]
) -> None:
    history, _ = league
    cutoff = history.matches["kickoff_utc"].iloc[-40]
    view = history.view(cutoff)
    fid = history.matches["fixture_id"].iloc[-1]
    with pytest.raises(LeakageError):
        view.odds_as_of(fid, cutoff + pd.Timedelta(minutes=1))
    odds = view.odds_as_of(fid, cutoff - pd.Timedelta(days=400), odds_type="prematch")
    assert odds is None  # the synthetic odds appear 20 hours before kick-off
    snap = snapshot(FeatureBuilder(TIERS).new_state())
    assert snap.teams == {}
