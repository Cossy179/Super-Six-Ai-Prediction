import numpy as np
import pandas as pd
import pytest

from super6.features.point_in_time import LeakageError, MatchHistory
from super6.models import build_model
from super6.models.calibration import fit_rate_calibration
from super6.synthetic import SyntheticTruth


def test_recovers_shrinkage_of_overconfident_rates() -> None:
    rng = np.random.default_rng(0)
    true_log = rng.normal(0.25, 0.35, 20_000)
    goals = rng.poisson(np.exp(true_log))
    # An overconfident forecaster exaggerates deviations from the mean by 1.6x.
    pred_log = 0.25 + 1.6 * (true_log - 0.25)
    a, s = fit_rate_calibration(pred_log, goals)
    assert s == pytest.approx(1 / 1.6, abs=0.04)
    assert a + s * 0.25 == pytest.approx(0.25, abs=0.03)
    # A well-calibrated forecaster is left (almost) alone.
    a2, s2 = fit_rate_calibration(true_log, goals)
    assert s2 == pytest.approx(1.0, abs=0.04) and a2 == pytest.approx(0.0, abs=0.04)


def test_earlier_views_only(league: tuple[MatchHistory, SyntheticTruth]) -> None:
    history, _ = league
    view = history.view(history.matches["kickoff_utc"].iloc[-30])
    earlier = view.at(view.cutoff - pd.Timedelta(days=30))
    assert earlier.n_results < view.n_results
    with pytest.raises(LeakageError):
        view.at(view.cutoff + pd.Timedelta(seconds=1))


def test_calibrated_model_wraps_base_and_reports_parameters() -> None:
    from super6.synthetic import synthetic_history

    history, _ = synthetic_history(n_teams=16, seasons=4, seed=5)
    m = history.matches
    cutoff = m["kickoff_utc"].iloc[-12] - pd.Timedelta(minutes=1)
    view = history.view(cutoff)
    model = build_model("catboost_cal", {"calibration_days": 200,
                                         "min_calibration_fixtures": 50})
    fitted = model.fit(view)
    info = fitted.describe()["calibration"]
    assert info["fixtures"] >= 50 and info["note"] is None
    # Too little history for the early fit: calibration is skipped, and says so.
    small = build_model("catboost_cal").fit(
        history.view(m["kickoff_utc"].iloc[300] - pd.Timedelta(minutes=1)))
    assert small.note == "CALIBRATION_SKIPPED_INSUFFICIENT_HISTORY"  # type: ignore[attr-defined]
    assert small.params == (0.0, 1.0, 0.0, 1.0)  # type: ignore[attr-defined]
    r = m.loc[m["kickoff_utc"] > cutoff].iloc[0]
    from super6.domain import FixtureSpec

    fx = FixtureSpec(r["fixture_id"], r["competition"], r["home_team"], r["away_team"],
                     r["kickoff_utc"].to_pydatetime())
    pred = fitted.predict(fx, view)
    a_h, s_h, _, _ = fitted.params  # type: ignore[attr-defined]
    raw_h = pred.details["uncalibrated_lambda"][0]
    assert pred.lam_home == pytest.approx(np.exp(a_h + s_h * np.log(raw_h)))
    assert pred.distribution.invariant_violations() == []
