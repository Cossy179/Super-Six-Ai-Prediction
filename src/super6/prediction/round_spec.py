"""Manually verified Super 6 round files (FR-01).

The official fixtures and deadline are entered by a person after checking the official
site; the tool does not scrape or submit entries. A round file is rejected (the run is
BLOCKED) when its fixture count, rules version, teams, competitions or timings fail checks.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from super6.domain import FixtureSpec, MarketOdds
from super6.ingestion.adapters.football_data_couk import fixture_key
from super6.ingestion.canonicalize import TeamRegistry, UnknownTeamError
from super6.rules import CompetitionRules
from super6.timeutil import UK

ROUND_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


class OddsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    home: float = Field(gt=1.0)
    draw: float = Field(gt=1.0)
    away: float = Field(gt=1.0)
    over25: float | None = Field(default=None, gt=1.0)
    under25: float | None = Field(default=None, gt=1.0)
    available_at: AwareDatetime
    source: str = "manual"


class FixtureInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    home: str
    away: str
    competition: str
    kickoff_utc: AwareDatetime
    neutral: bool = False
    odds: OddsInput | None = None


class RoundSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    round_id: str
    deadline_utc: AwareDatetime
    rules_version: str
    illustrative: bool = False
    source_note: str | None = None
    fixtures: list[FixtureInput]

    @field_validator("round_id")
    @classmethod
    def _round_id(cls, v: str) -> str:
        if not ROUND_ID.match(v):
            raise ValueError("round_id must be 3-64 chars of a-z, 0-9, '.', '_' or '-'")
        return v


def load_round(path: str | Path) -> RoundSpec:
    with Path(path).open(encoding="utf-8") as fh:
        return RoundSpec.model_validate(yaml.safe_load(fh))


@dataclass
class ResolvedRound:
    spec: RoundSpec
    fixtures: list[FixtureSpec]
    odds: dict[str, MarketOdds] = field(default_factory=dict)
    block_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def resolve_round(spec: RoundSpec, registry: TeamRegistry, rules: CompetitionRules,
                  known_competitions: set[str]) -> ResolvedRound:
    out = ResolvedRound(spec, [])
    if spec.rules_version != rules.rules_version:
        out.block_reasons.append(
            f"RULES_VERSION_MISMATCH: round expects {spec.rules_version!r}, loaded rules are "
            f"{rules.rules_version!r}")
    if len(spec.fixtures) != rules.fixtures_per_round:
        out.block_reasons.append(
            f"WRONG_FIXTURE_COUNT: {len(spec.fixtures)} (rules need {rules.fixtures_per_round})")
    if spec.illustrative:
        out.warnings.append("ILLUSTRATIVE_DATA_ONLY")
    for fx in spec.fixtures:
        try:
            home = registry.resolve(fx.home)
            away = registry.resolve(fx.away)
        except UnknownTeamError as exc:
            out.block_reasons.append(f"UNKNOWN_TEAM: {exc.args[0]}")
            continue
        if fx.competition not in known_competitions:
            out.block_reasons.append(f"COMPETITION_NOT_CONFIGURED: {fx.competition}")
            continue
        if home == away:
            out.block_reasons.append(f"SAME_TEAM: {fx.home} vs {fx.away}")
            continue
        local_date = fx.kickoff_utc.astimezone(UK).date()
        spec_fx = FixtureSpec(fixture_key(fx.competition, local_date, home, away),
                              fx.competition, home, away, fx.kickoff_utc, fx.neutral)
        out.fixtures.append(spec_fx)
        if fx.odds is not None:
            out.odds[spec_fx.fixture_id] = MarketOdds(
                home=fx.odds.home, draw=fx.odds.draw, away=fx.odds.away,
                over25=fx.odds.over25, under25=fx.odds.under25,
                available_at=fx.odds.available_at, source=fx.odds.source)
    teams = Counter(t for f in out.fixtures for t in (f.home_team, f.away_team))
    repeated = sorted(t for t, n in teams.items() if n > 1)
    if repeated:
        out.block_reasons.append(f"TEAM_IN_MULTIPLE_FIXTURES: {', '.join(repeated)}")
    if out.fixtures:
        first = min(f.kickoff_utc for f in out.fixtures)
        if spec.deadline_utc > first:
            out.block_reasons.append(
                f"DEADLINE_AFTER_KICKOFF: deadline {spec.deadline_utc} is after the first "
                f"kick-off {first}")
        elif spec.deadline_utc < first:
            # The terms say a round closes at the first kick-off; an earlier deadline is
            # safe but unusual, so flag a likely typo.
            out.warnings.append(
                f"DEADLINE_BEFORE_FIRST_KICKOFF: rounds close at the first kick-off ({first})")
    return out
