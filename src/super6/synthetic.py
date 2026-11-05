"""Synthetic leagues with known parameters, for tests and demonstrations only.

Nothing generated here is real football data; team names are placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from super6.domain import FixtureStatus
from super6.features.point_in_time import MatchHistory, empty_odds_frame
from super6.prediction.score_grid import dixon_coles_distribution
from super6.timeutil import UK


@dataclass(frozen=True)
class SyntheticTruth:
    attack: dict[str, float]
    defence: dict[str, float]
    mu: float
    home: float
    rho: float


def _round_robin(teams: list[str]) -> list[list[tuple[str, str]]]:
    """Circle-method double round robin: each pair meets once at each venue."""
    ts = list(teams)
    if len(ts) % 2:
        ts.append("__bye__")
    n = len(ts)
    rounds: list[list[tuple[str, str]]] = []
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = ts[i], ts[n - 1 - i]
            if "__bye__" in (a, b):
                continue
            pairs.append((a, b) if r % 2 == 0 else (b, a))
        rounds.append(pairs)
        ts = [ts[0], ts[-1], *ts[1:-1]]
    return rounds + [[(b, a) for a, b in rnd] for rnd in rounds]


def synthetic_history(
    n_teams: int = 12,
    seasons: int = 3,
    seed: int = 1,
    competition: str = "SYN",
    mu: float = 0.15,
    home: float = 0.25,
    rho: float = -0.08,
    strength_sd: float = 0.3,
    first_saturday: datetime = datetime(2021, 8, 7, tzinfo=UTC),
    with_odds: bool = False,
) -> tuple[MatchHistory, SyntheticTruth]:
    rng = np.random.default_rng(seed)
    teams = [f"team-{i:02d}" for i in range(n_teams)]
    att = dict(zip(teams, rng.normal(0, strength_sd, n_teams), strict=True))
    dfn = dict(zip(teams, rng.normal(0, strength_sd, n_teams), strict=True))
    rows = []
    odds_rows = []
    day = first_saturday
    schedule = _round_robin(teams)
    for season in range(seasons):
        season_label = f"{2021 + season}-{(22 + season) % 100:02d}"
        for rnd in schedule:
            kickoff = datetime(day.year, day.month, day.day, 15, 0, tzinfo=UK).astimezone(UTC)
            for h, a in rnd:
                lam_h = float(np.exp(mu + home + att[h] - dfn[a]))
                lam_a = float(np.exp(mu + att[a] - dfn[h]))
                dist, _ = dixon_coles_distribution(lam_h, lam_a, rho)
                flat = dist.grid.ravel() / dist.grid.sum()
                cell = int(rng.choice(flat.size, p=flat))
                hg, ag = divmod(cell, dist.size)
                fid = f"{competition}-{season_label}-{h}-{a}"
                rows.append(
                    {
                        "fixture_id": fid,
                        "competition": competition,
                        "season": season_label,
                        "kickoff_utc": kickoff,
                        "kickoff_time_known": True,
                        "home_team": h,
                        "away_team": a,
                        "status": FixtureStatus.FINISHED.value,
                        "home_goals": hg,
                        "away_goals": ag,
                        "result_available_at": kickoff + timedelta(minutes=135),
                    }
                )
                if with_odds:
                    out = dist.outcome_probabilities()
                    margin = 1.05
                    odds_rows.append(
                        {
                            "fixture_id": fid,
                            "odds_type": "prematch",
                            "source": "synthetic",
                            "available_at": kickoff - timedelta(hours=20),
                            "home": 1 / (out.home * margin),
                            "draw": 1 / (out.draw * margin),
                            "away": 1 / (out.away * margin),
                            "over25": np.nan,
                            "under25": np.nan,
                        }
                    )
            day += timedelta(days=7)
        day += timedelta(days=70)  # close season
    odds = pd.DataFrame(odds_rows) if odds_rows else empty_odds_frame()
    history = MatchHistory(pd.DataFrame(rows), odds)
    return history, SyntheticTruth(att, dfn, mu, home, rho)
