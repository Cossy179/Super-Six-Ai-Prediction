"""Rolling team-form features computed only from results already in a point-in-time view."""

from __future__ import annotations

import pandas as pd


def long_format(results: pd.DataFrame) -> pd.DataFrame:
    """One row per team per match: team, opponent, venue, goals for/against."""
    home = pd.DataFrame(
        {
            "fixture_id": results["fixture_id"],
            "kickoff_utc": results["kickoff_utc"],
            "competition": results["competition"],
            "team": results["home_team"],
            "opponent": results["away_team"],
            "is_home": True,
            "goals_for": results["home_goals"],
            "goals_against": results["away_goals"],
        }
    )
    away = pd.DataFrame(
        {
            "fixture_id": results["fixture_id"],
            "kickoff_utc": results["kickoff_utc"],
            "competition": results["competition"],
            "team": results["away_team"],
            "opponent": results["home_team"],
            "is_home": False,
            "goals_for": results["away_goals"],
            "goals_against": results["home_goals"],
        }
    )
    out = pd.concat([home, away], ignore_index=True)
    return out.sort_values(["kickoff_utc", "fixture_id", "is_home"]).reset_index(drop=True)


def team_form(results: pd.DataFrame, window: int) -> pd.DataFrame:
    """Goals for/against over each team's last ``window`` matches (any venue).

    Returns a frame indexed by team with columns ``matches``, ``goals_for``,
    ``goals_against`` (totals, not averages) and ``last_kickoff``.
    """
    if window <= 0:
        raise ValueError("window must be positive")
    lf = long_format(results)
    if lf.empty:
        return pd.DataFrame(columns=["matches", "goals_for", "goals_against", "last_kickoff"])
    recent = lf.groupby("team", sort=True).tail(window)
    return recent.groupby("team").agg(
        matches=("fixture_id", "size"),
        goals_for=("goals_for", "sum"),
        goals_against=("goals_against", "sum"),
        last_kickoff=("kickoff_utc", "max"),
    )


def rest_days(results: pd.DataFrame, team: str, at: pd.Timestamp) -> float | None:
    """Days between ``team``'s most recent visible match and ``at`` (None if unseen)."""
    played = results.loc[(results["home_team"] == team) | (results["away_team"] == team)]
    if played.empty:
        return None
    return float((at - played["kickoff_utc"].max()).total_seconds() / 86400.0)
