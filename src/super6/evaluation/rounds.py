"""Replay genuine historical Super 6 rounds (DESIGN.md §13.2).

Given verified round files (the six fixtures the competition actually chose, with their
real deadlines), each round is forecast as of one minute before its deadline and then
scored against the ingested results. The output is labelled ``historical`` to keep it
separate from synthetic six-match batches.

Real round lists are not bundled. Super 6 publishes no archive this project may scrape,
so add verified files to a directory such as ``rounds/history/``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from typing import Any

import pandas as pd

from super6.domain import FixtureStatus
from super6.features.point_in_time import MatchHistory
from super6.ingestion.canonicalize import TeamRegistry
from super6.models.base import ScoreModel
from super6.prediction.optimiser import Objective, super6_points
from super6.prediction.round_spec import RoundSpec, resolve_round
from super6.prediction.service import forecast_round
from super6.rules import CompetitionRules


def replay_rounds(
    history: MatchHistory,
    specs: Sequence[RoundSpec],
    registry: TeamRegistry,
    rules: CompetitionRules,
    model_factory: Callable[[], ScoreModel],
    known_competitions: set[str],
    golden_goal_config: Mapping[str, Any] | None = None,
    allow_illustrative: bool = False,
) -> pd.DataFrame:
    finished = history.matches.loc[history.matches["status"] == FixtureStatus.FINISHED.value]
    actual = {fid: (int(h), int(a)) for fid, h, a in zip(
        finished["fixture_id"], finished["home_goals"], finished["away_goals"], strict=True)}
    rows: list[dict[str, Any]] = []
    for spec in sorted(specs, key=lambda s: s.deadline_utc):
        base: dict[str, Any] = {"round_id": spec.round_id, "deadline_utc": spec.deadline_utc,
                                "kind": "illustrative" if spec.illustrative else "historical"}
        if spec.illustrative and not allow_illustrative:
            rows.append({**base, "status": "SKIPPED_ILLUSTRATIVE"})
            continue
        resolved = resolve_round(spec, registry, rules, known_competitions)
        if resolved.block_reasons:
            rows.append({**base, "status": "BLOCKED",
                         "detail": "; ".join(resolved.block_reasons)})
            continue
        cutoff = spec.deadline_utc - timedelta(minutes=1)
        model = model_factory()
        fc = forecast_round(history, resolved, cutoff, model, rules, golden_goal_config)
        missing = [f.fixture_id for f in fc.fixtures if f.fixture_id not in actual]
        out = {**base, "status": "SCORED" if not missing else "PARTIAL", "model": model.name,
               "fixtures_scored": len(fc.fixtures) - len(missing),
               "void_or_missing": ";".join(missing),
               "golden_goal_minute": fc.golden_goal.minute if fc.golden_goal else None}
        for obj in Objective:
            pts, exact = 0, 0
            for fp in fc.picks[obj].fixtures:
                if fp.fixture_id not in actual:
                    continue
                hg, ag = actual[fp.fixture_id]
                p = super6_points(fp.pick.home_goals, fp.pick.away_goals, hg, ag, rules)
                pts += p
                exact += int(p == rules.points_exact_score)
            out[f"{obj.value}_points"] = pts
            out[f"{obj.value}_exact"] = exact
            out[f"{obj.value}_expected_points"] = fc.picks[obj].expected_points
        out["jackpot_all_exact"] = out["jackpot_exact"] == len(fc.fixtures) and not missing
        out["jackpot_joint_probability"] = fc.picks[Objective.JACKPOT].joint_exact_probability
        rows.append(out)
    return pd.DataFrame(rows)


def summarise_rounds(frame: pd.DataFrame) -> dict[str, Any]:
    scored = frame.loc[frame["status"].isin(["SCORED", "PARTIAL"])] if len(frame) else frame
    if scored.empty:
        return {"rounds": 0}
    return {
        "rounds": len(scored),
        "kind": sorted(set(scored["kind"])),
        "points_mode_mean": float(scored["points_points"].mean()),
        "points_mode_expected_mean": float(scored["points_expected_points"].mean()),
        "jackpot_mode_mean": float(scored["jackpot_points"].mean()),
        "exact_hits_points_mode": {
            str(k): int(v) for k, v in scored["points_exact"].value_counts().sort_index().items()
        },
        "jackpots": int(scored["jackpot_all_exact"].sum()),
        "blocked_or_skipped": int((~frame["status"].isin(["SCORED", "PARTIAL"])).sum()),
    }
