"""Versioned competition rules (DESIGN.md §3.5).

The rules object is deliberately strict: unknown keys, unsupported conventions, or an
inconsistent points table raise ``RulesValidationError`` so that a rule change we do not
understand blocks predictions instead of silently altering the optimiser.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from super6.config import load_yaml

SUPPORTED_SCHEMA_VERSION = 1


class RulesValidationError(ValueError):
    """Raised when the competition rules cannot be used safely."""


class CompetitionRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    competition: Literal["sky_super_6"]
    rules_version: str = Field(min_length=1)
    verified: bool
    verified_on: date | None = None
    source_urls: tuple[str, ...] = ()

    fixtures_per_round: int = Field(gt=0, le=20)
    points_exact_score: int = Field(ge=0)
    points_correct_result_only: int = Field(ge=0)
    points_incorrect: int = Field(ge=0)

    golden_goal_enabled: bool
    golden_goal_minute_indexing: Literal["one_based"]
    golden_goal_tiebreak: Literal["closest_minute", "exact_minute"]
    golden_goal_stoppage_time: Literal["capped_at_period_end", "continuous_clock"]
    # Which goal is "the first goal" when kick-offs are staggered: the first in real
    # (wall-clock) time, or the one with the lowest match-clock minute in any match.
    golden_goal_first_goal: Literal["first_in_real_time", "earliest_match_minute"]

    settlement_period: Literal["full_time"]
    submission_policy: Literal["manual_only"]

    @model_validator(mode="after")
    def _check_points_order(self) -> CompetitionRules:
        if not (
            self.points_exact_score >= self.points_correct_result_only >= self.points_incorrect
        ):
            raise ValueError(
                "points must satisfy exact_score >= correct_result_only >= incorrect"
            )
        if self.verified and self.verified_on is None:
            raise ValueError("verified rules must record verified_on")
        return self

    def warnings(self) -> list[str]:
        out: list[str] = []
        if not self.verified:
            out.append("RULES_UNVERIFIED")
        return out


def load_rules(path: str | Path = "competition_rules.yaml") -> CompetitionRules:
    raw = load_yaml(path)
    try:
        return CompetitionRules.model_validate(raw)
    except ValidationError as exc:
        raise RulesValidationError(f"competition rules rejected: {exc}") from exc
