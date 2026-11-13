"""Request and response shapes for the API (also the source of its OpenAPI document)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Phase = Literal["upcoming", "in_play", "awaiting_results", "settled"]
# Round ids become file names (rounds/<id>.yaml), so they can never contain a path separator.
ROUND_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{2,63}$"


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- shared
class Score(ApiModel):
    home_goals: int
    away_goals: int


class ScoreProbabilityOut(Score):
    probability: float


class Outcome(ApiModel):
    home: float
    draw: float
    away: float


class ExpectedGoals(ApiModel):
    home: float
    away: float


class WarningNote(ApiModel):
    """A warning as stored, with its code and a plain-English meaning."""

    text: str
    code: str
    meaning: str


# --------------------------------------------------------------------------- health
class Check(ApiModel):
    name: str
    status: Literal["ok", "warning", "error"]
    detail: str


class SourceFreshness(ApiModel):
    source: str
    snapshots: int
    latest_retrieved_at: datetime | None


class OddsCoverage(ApiModel):
    odds_type: str
    availability_basis: str
    snapshots: int
    fixtures: int


class AlertOut(ApiModel):
    severity: str
    code: str
    message: str
    round_id: str | None
    at: datetime


class HealthResponse(ApiModel):
    status: Literal["ok", "degraded", "error"]
    generated_at: datetime
    code_version: str
    rules_version: str
    rules_verified: bool
    default_model: str
    golden_goal_profile: str
    admin_enabled: bool
    checks: list[Check]
    latest_result_at: datetime | None
    sources: list[SourceFreshness]
    odds: list[OddsCoverage]
    goal_events: int
    quality_issues: dict[str, int]
    last_cycle_at: datetime | None
    recent_alerts: list[AlertOut]
    attributions: list[str]


# --------------------------------------------------------------------------- rounds
class RoundFixture(ApiModel):
    fixture_id: str
    competition: str
    home_team_id: str
    away_team_id: str
    home_team: str
    away_team: str
    kickoff_utc: datetime
    status: str
    result: Score | None


class RunSummary(ApiModel):
    run_id: str
    status: str
    created_at: datetime
    cutoff_at: datetime
    locked_at: datetime | None
    model_version: str


class RoundListItem(ApiModel):
    round_id: str
    deadline_at: datetime
    status: str
    phase: Phase
    confirmed: bool
    locked: bool
    official_run_id: str | None


class RoundResponse(RoundListItem):
    rules_version: str
    confirmed_at: datetime | None
    confirmed_source: str | None
    source_note: str | None
    fixtures: list[RoundFixture]
    runs: list[RunSummary]


# --------------------------------------------------------------------------- predictions
class FixtureForecastOut(ApiModel):
    fixture_id: str
    competition: str
    home_team: str
    away_team: str
    kickoff_utc: datetime
    expected_goals: ExpectedGoals
    outcome_probabilities: Outcome
    tail_probability: float
    top_scores: list[ScoreProbabilityOut]
    forecast_basis: str
    warnings: list[str]
    result: Score | None


class Alternative(Score):
    probability: float
    expected_points: float


class Pick(ApiModel):
    fixture_id: str
    predicted_home_goals: int
    predicted_away_goals: int
    exact_score_probability: float
    correct_result_probability: float
    expected_points: float
    top_alternatives: list[Alternative]
    points_scored: int | None = Field(description="Set once the fixture has a result")


class ModePicks(ApiModel):
    expected_points: float
    joint_exact_probability: float
    joint_probability_assumptions: list[str]
    fixtures: list[Pick]
    points_scored: int | None
    exact_hits: int | None
    entry_summary: str = Field(description="Copy-friendly list of the six picks")


class GoldenGoalOut(ApiModel):
    minute: int
    method: str
    expected_absolute_error_minutes: float
    interval_80: tuple[int, int]
    p_no_goal: float
    assumptions: list[str]
    distribution: dict[str, float] = Field(description="Recorded minute -> probability")
    actual_minute: int | None
    absolute_error: int | None
    actual_source: Literal["official", "goal_minutes"] | None


class PredictionsResponse(ApiModel):
    round_id: str
    prediction_run_id: str
    status: str
    is_locked: bool = Field(description="False means the picks can still change")
    generated_at: datetime
    cutoff_at: datetime
    deadline_at: datetime
    locked_at: datetime | None
    model_version: str
    rules_version: str
    default_mode: str
    tie_break_rule: str
    fixtures: list[FixtureForecastOut]
    modes: dict[str, ModePicks]
    differing_fixture_ids: list[str] = Field(
        description="Fixtures where points mode and jackpot mode pick different scores")
    golden_goal: GoldenGoalOut | None
    warnings: list[WarningNote]
    block_reasons: list[str]


class DistributionResponse(ApiModel):
    fixture_id: str
    run_id: str
    round_id: str
    home_team: str
    away_team: str
    kickoff_utc: datetime
    forecast_basis: str
    expected_goals: ExpectedGoals
    outcome_probabilities: Outcome
    tail_probability: float = Field(description="Probability of a score beyond the grid")
    max_goals: int
    grid: list[list[float]] = Field(description="grid[home_goals][away_goals]")
    home_goals_distribution: list[float]
    away_goals_distribution: list[float]
    top_scores: list[ScoreProbabilityOut]


class FormMatch(ApiModel):
    kickoff_utc: datetime
    competition: str
    opponent: str
    venue: Literal["home", "away"]
    goals_for: int
    goals_against: int


class OddsUsed(ApiModel):
    source: str
    odds_type: str
    available_at: datetime
    availability_basis: str
    home: float
    draw: float
    away: float
    over25: float | None
    under25: float | None
    implied: Outcome = Field(description="Bookmaker margin removed proportionally")


class ExplanationResponse(ApiModel):
    fixture_id: str
    run_id: str
    round_id: str
    home_team: str
    away_team: str
    model_version: str
    forecast_basis: str
    summary: list[str] = Field(description="Plain-English statements of what the model used")
    expected_goals: ExpectedGoals
    results_model_expected_goals: ExpectedGoals | None
    market_expected_goals: ExpectedGoals | None
    odds: OddsUsed | None
    home_form: list[FormMatch]
    away_form: list[FormMatch]
    picks: dict[str, Pick]
    warnings: list[WarningNote]


# --------------------------------------------------------------------------- performance
class LiveRound(ApiModel):
    round_id: str
    deadline_at: datetime
    run_id: str
    record_type: Literal["locked", "unlocked"] = Field(
        description="unlocked: no forecast was locked before the deadline, so indicative only")
    fixtures_settled: int
    points_mode_points: int
    points_mode_exact: int
    points_mode_expected: float
    jackpot_mode_points: int
    jackpot_mode_exact: int
    exact_log_loss_mean: float | None
    rps_mean: float | None
    golden_goal_predicted: int | None
    golden_goal_actual: int | None
    golden_goal_abs_error: int | None
    golden_goal_source: Literal["official", "goal_minutes"] | None


class LiveTotals(ApiModel):
    rounds: int
    locked_rounds: int
    points_mode_mean: float | None
    points_mode_expected_mean: float | None
    jackpot_mode_mean: float | None
    exact_hits_points_mode: int
    exact_hits_jackpot_mode: int
    golden_goal_rounds: int
    golden_goal_abs_error_mean: float | None


class LivePerformance(ApiModel):
    rounds: list[LiveRound]
    totals: LiveTotals


class BacktestModel(ApiModel):
    model: str
    fixtures: int
    exact_log_loss: float
    rps: float | None
    points_per_fixture: float | None
    jackpot_points_per_fixture: float | None
    exact_score_rate: float | None


class Backtest(ApiModel):
    label: str
    logged_at: datetime | None
    window: tuple[str, str]
    competitions: list[str]
    code_version: str | None
    reference: str | None
    models: list[BacktestModel]


class PerformanceResponse(ApiModel):
    live: LivePerformance
    headline_backtest: Backtest | None
    backtests: list[Backtest]
    notes: list[str]


# --------------------------------------------------------------------------- models
class ModelInfo(ApiModel):
    name: str
    description: str
    role: Literal["default", "component", "challenger", "baseline"]
    config: dict[str, Any]


class RegistryEntry(ApiModel):
    id: int
    model_name: str
    code_version: str
    training_cutoff: datetime
    created_at: datetime
    training_matches: int | None


class ModelsResponse(ApiModel):
    default_model: str
    golden_goal: dict[str, Any]
    models: list[ModelInfo]
    registry: list[RegistryEntry]
    evidence: str


# --------------------------------------------------------------------------- admin
class ImportRoundRequest(ApiModel):
    text: str = Field(min_length=3, max_length=2000,
                      description='The six matchups, e.g. "Fulham v Newcastle, ..."')
    round_number: int | None = Field(default=None, ge=1, le=99)
    round_date: date | None = Field(default=None, description="Main match day (UK)")
    confirmed: bool = Field(
        default=False, description="True only if the six were checked against the Super 6 app")
    confirmed_source: str | None = Field(default=None, min_length=3, max_length=200)


class ImportedFixtureOut(ApiModel):
    home_team: str
    away_team: str
    competition: str
    kickoff_utc: datetime
    kickoff_source: str


class ImportRoundResponse(ApiModel):
    round_id: str
    deadline_at: datetime
    confirmed: bool
    fixtures: list[ImportedFixtureOut]
    warnings: list[str]
    block_reasons: list[str]


class ConfirmRoundRequest(ApiModel):
    source: str = Field(default="Checked against the Super 6 app", min_length=3,
                        max_length=200)


class GenerateRequest(ApiModel):
    round_id: str = Field(pattern=ROUND_ID_PATTERN)
    publish: bool = True
    refresh_data: bool = Field(
        default=False, description="Download the provider's upcoming fixtures and odds first")


class GenerateResponse(ApiModel):
    round_id: str
    run_id: str
    status: str
    created: bool
    reason: Literal["new forecast", "forecast unchanged", "already locked"]
    warnings: list[str]
    block_reasons: list[str]


class LockResponse(ApiModel):
    run_id: str
    round_id: str
    status: str
    locked_at: datetime


class SettleRequest(ApiModel):
    golden_goal_minute: int | None = Field(
        default=None, ge=0, le=90, description="Official answer from the app (0 = no goals)")
    allow_missing: bool = False


class SettlementOut(ApiModel):
    run_id: str
    status: str
    settled_fixtures: int
    missing_fixtures: list[str]
    metrics: dict[str, dict[str, float]]


class SettleResponse(ApiModel):
    round_id: str
    settlements: list[SettlementOut]
