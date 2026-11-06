"""Leakage tests (DESIGN.md §19.3): future information must not change past forecasts."""

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from super6.domain import FixtureSpec, FixtureStatus
from super6.evaluation.replay import walk_forward
from super6.features.point_in_time import LeakageError, MatchHistory
from super6.models import MODEL_CLASSES, build_model
from super6.rules import CompetitionRules
from super6.synthetic import SyntheticTruth


def _split(history: MatchHistory, n_future: int = 40) -> tuple[pd.Timestamp, pd.DataFrame]:
    m = history.matches
    cutoff = m["kickoff_utc"].iloc[-n_future] - pd.Timedelta(minutes=1)
    return cutoff, m.loc[m["kickoff_utc"] > cutoff]


def _poisoned(history: MatchHistory, cutoff: pd.Timestamp) -> MatchHistory:
    """Overwrite every post-cutoff result with absurd scores and add absurd future odds."""
    m = history.matches.copy()
    future = m["kickoff_utc"] > cutoff
    m.loc[future, "home_goals"] = 9
    m.loc[future, "away_goals"] = 0
    # A result published one second after the cutoff for a match before it.
    late = m.index[(m["kickoff_utc"] <= cutoff)][-1]
    m.loc[late, ["home_goals", "away_goals"]] = [7, 7]
    m.loc[late, "result_available_at"] = cutoff + pd.Timedelta(seconds=1)
    odds = history.odds.copy()
    extra = odds.loc[odds["fixture_id"].isin(m.loc[future, "fixture_id"])].copy()
    extra["available_at"] = cutoff + pd.Timedelta(minutes=5)  # e.g. closing odds
    extra["odds_type"] = "closing"
    extra[["home", "draw", "away"]] = [1.01, 30.0, 60.0]
    return MatchHistory(m, pd.concat([odds, extra], ignore_index=True))


def _upcoming(future: pd.DataFrame) -> list[FixtureSpec]:
    return [FixtureSpec(r.fixture_id, r.competition, r.home_team, r.away_team,
                        r.kickoff_utc.to_pydatetime()) for r in future.head(6).itertuples()]


def test_view_excludes_unpublished_results_and_future_odds(
    league: tuple[MatchHistory, SyntheticTruth]
) -> None:
    history, _ = league
    cutoff, future = _split(history)
    poisoned = _poisoned(history, cutoff)
    view = poisoned.view(cutoff)
    assert (view.results["result_available_at"] <= cutoff).all()
    assert not set(view.results["fixture_id"]) & set(future["fixture_id"])
    odds = view.latest_odds(future["fixture_id"].iloc[0])
    assert odds is not None and odds.odds_type == "prematch"
    assert odds.available_at <= cutoff.to_pydatetime()


@pytest.mark.parametrize("name", sorted(MODEL_CLASSES))
def test_future_information_does_not_change_forecasts(
    name: str, league: tuple[MatchHistory, SyntheticTruth]
) -> None:
    history, _ = league
    cutoff, future = _split(history)
    poisoned = _poisoned(history, cutoff)
    clean_view, dirty_view = history.view(cutoff), poisoned.view(cutoff)
    late = poisoned.matches.loc[poisoned.matches["result_available_at"]
                                == cutoff + pd.Timedelta(seconds=1), "fixture_id"]
    # The clean history publishes that late result before the cutoff, so drop it there too
    # to compare like with like.
    clean = MatchHistory(history.matches.loc[~history.matches["fixture_id"].isin(late)],
                         history.odds)
    clean_view = clean.view(cutoff)
    assert clean_view.fingerprint() == dirty_view.fingerprint()
    model = build_model(name)
    a, b = model.fit(clean_view), model.fit(dirty_view)
    for fx in _upcoming(future):
        pa, pb = a.predict(fx, clean_view), b.predict(fx, dirty_view)
        assert np.array_equal(pa.distribution.grid, pb.distribution.grid), fx.fixture_id


def test_model_cannot_be_used_with_an_earlier_view(
    league: tuple[MatchHistory, SyntheticTruth]
) -> None:
    history, _ = league
    cutoff, future = _split(history)
    fitted = build_model("poisson").fit(history.view(cutoff))
    earlier = history.view(cutoff - timedelta(days=7))
    with pytest.raises(LeakageError):
        fitted.predict(_upcoming(future)[0], earlier)


def test_walk_forward_is_unaffected_by_later_results(
    league: tuple[MatchHistory, SyntheticTruth], rules: CompetitionRules
) -> None:
    history, _ = league
    m = history.matches
    start = m["kickoff_utc"].iloc[-60]
    end = m["kickoff_utc"].iloc[-30]
    models = {"poisson": build_model("poisson"), "rolling_goals": build_model("rolling_goals")}
    base = walk_forward(history, models, rules, start, end)
    # Corrupt results strictly after the test window; earlier forecasts must not move.
    later = m["kickoff_utc"] >= end
    corrupted = m.copy()
    corrupted.loc[later, ["home_goals", "away_goals"]] = [0, 8]
    again = walk_forward(MatchHistory(corrupted, history.odds), models, rules, start, end)
    cols = ["model", "fixture_id", "p_home", "p_draw", "p_away", "exact_log_loss",
            "points_pick"]
    pd.testing.assert_frame_equal(base[cols], again[cols])
    assert (base["cutoff"] < pd.to_datetime(base["kickoff_utc"], utc=True)).all()


def test_scheduled_fixtures_never_enter_training(
    league: tuple[MatchHistory, SyntheticTruth]
) -> None:
    history, _ = league
    m = history.matches.copy()
    tail = m.index[-5:]
    m.loc[tail, "status"] = FixtureStatus.SCHEDULED.value
    m.loc[tail, ["home_goals", "away_goals", "result_available_at"]] = np.nan
    view = MatchHistory(m, history.odds).view(m["kickoff_utc"].max() + pd.Timedelta(days=30))
    assert not set(view.results["fixture_id"]) & set(m.loc[tail, "fixture_id"])


def test_restrict_odds_keeps_only_the_named_timestamp_bases(
    league: tuple[MatchHistory, SyntheticTruth]
) -> None:
    history, _ = league
    cutoff, future = _split(history)
    odds = history.odds.copy()
    odds["availability_basis"] = "inferred"  # like historical provider files
    target = future["fixture_id"].iloc[0]
    observed = odds.loc[odds["fixture_id"] == target].head(1).copy()
    observed["availability_basis"] = "observed"
    observed["available_at"] = cutoff - pd.Timedelta(hours=3)
    observed[["home", "draw", "away"]] = [2.0, 3.5, 4.0]
    mixed = MatchHistory(history.matches, pd.concat([odds, observed], ignore_index=True))
    # Without the restriction, the latest snapshot wins whatever its basis.
    only_observed = mixed.restrict_odds({"observed"})
    assert set(only_observed.odds["availability_basis"]) == {"observed"}
    seen = only_observed.view(cutoff).latest_odds(target)
    assert seen is not None and seen.home == 2.0
    # Other fixtures have no observed odds, so the market model can't use them.
    other = future["fixture_id"].iloc[1]
    assert only_observed.view(cutoff).latest_odds(other) is None
    assert mixed.view(cutoff).latest_odds(other) is not None
    # Odds from a frame without the column are "unknown", never silently "observed".
    assert set(history.odds["availability_basis"]) == {"unknown"}
    with pytest.raises(ValueError, match="unknown odds bases"):
        mixed.restrict_odds({"verified"})
