"""Relational schema (DESIGN.md §14). PostgreSQL in deployment; SQLite works for local use.

Audit rules enforced here rather than by convention:

* every timestamp column is timezone-aware UTC;
* a prediction run's body (forecasts, score grid, recommendations, Golden Goal) is
  immutable once the run is LOCKED or SETTLED. The ``before_flush`` hook raises
  ``ImmutableRecordError`` on any insert, update, or delete that would change it. The only
  permitted change to a locked run is the LOCKED → SETTLED transition.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


class ImmutableRecordError(RuntimeError):
    """Attempt to modify a locked prediction record."""


class UTCDateTime(TypeDecorator[datetime]):
    """Stores aware datetimes; always returns aware UTC (SQLite drops tzinfo otherwise)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime cannot be stored; use UTC")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    PUBLISHED = "PUBLISHED"
    LOCKED = "LOCKED"
    SETTLED = "SETTLED"
    BLOCKED = "BLOCKED"


ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.DRAFT: {RunStatus.VALIDATED, RunStatus.BLOCKED},
    RunStatus.VALIDATED: {RunStatus.PUBLISHED, RunStatus.LOCKED, RunStatus.BLOCKED},
    RunStatus.PUBLISHED: {RunStatus.LOCKED, RunStatus.BLOCKED},
    RunStatus.LOCKED: {RunStatus.SETTLED},
    RunStatus.SETTLED: set(),
    RunStatus.BLOCKED: set(),
}
FROZEN_STATUSES = {RunStatus.LOCKED.value, RunStatus.SETTLED.value}


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: UTCDateTime(), dict[str, Any]: JSON, list[Any]: JSON,
    }


# --------------------------------------------------------------------------- reference data
class Competition(Base):
    __tablename__ = "competitions"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    country: Mapped[str] = mapped_column(String(64))
    pool: Mapped[str] = mapped_column(String(32))
    tier: Mapped[int] = mapped_column(Integer)


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(128))


class TeamAlias(Base):
    __tablename__ = "team_aliases"
    __table_args__ = (UniqueConstraint("source", "normalised"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    source: Mapped[str] = mapped_column(String(64))
    alias: Mapped[str] = mapped_column(String(128))
    normalised: Mapped[str] = mapped_column(String(128))


# --------------------------------------------------------------------------- provenance
class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (UniqueConstraint("source", "uri", "checksum"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64))
    uri: Mapped[str] = mapped_column(String(512))
    retrieved_at: Mapped[datetime]
    checksum: Mapped[str] = mapped_column(String(64))
    licence_id: Mapped[str] = mapped_column(String(64))
    local_path: Mapped[str | None] = mapped_column(String(512))
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    rows_accepted: Mapped[int] = mapped_column(Integer, default=0)
    quality_status: Mapped[str] = mapped_column(String(16))
    ingested_at: Mapped[datetime] = mapped_column(default=_now)


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("source_snapshots.id"))
    severity: Mapped[str] = mapped_column(String(16))
    code: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[str] = mapped_column(String(1024))
    record_ref: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(default=_now)


# --------------------------------------------------------------------------- fixtures
class Fixture(Base):
    __tablename__ = "fixtures"
    __table_args__ = (Index("ix_fixtures_teams", "competition_id", "home_team_id",
                            "away_team_id"),)
    # ID = competition-YYYYMMDD(local)-home-away; see football_data_couk.fixture_key.
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    competition_id: Mapped[str] = mapped_column(ForeignKey("competitions.id"), index=True)
    season: Mapped[str] = mapped_column(String(8))
    home_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    kickoff_utc: Mapped[datetime] = mapped_column(index=True)
    kickoff_time_known: Mapped[bool] = mapped_column(Boolean)
    neutral: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16))
    # Where the row came from: a data provider (e.g. football_data_couk), or "round_file"
    # when it was created only from a Super 6 round file and no provider has listed it yet.
    source: Mapped[str] = mapped_column(String(64), default="football_data_couk",
                                        server_default="football_data_couk")
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class FixtureResult(Base):
    __tablename__ = "fixture_results"
    __table_args__ = (UniqueConstraint("fixture_id", "source"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[str] = mapped_column(ForeignKey("fixtures.id"), index=True)
    source: Mapped[str] = mapped_column(String(64))
    home_goals: Mapped[int] = mapped_column(Integer)
    away_goals: Mapped[int] = mapped_column(Integer)
    result_available_at: Mapped[datetime]
    source_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("source_snapshots.id"))
    recorded_at: Mapped[datetime] = mapped_column(default=_now)


class GoalEvent(Base):
    """Goal timings for Golden Goal modelling and settlement (openfootball, CC0)."""

    __tablename__ = "goal_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[str] = mapped_column(ForeignKey("fixtures.id"), index=True)
    source: Mapped[str] = mapped_column(String(64))
    period: Mapped[int] = mapped_column(Integer)
    match_minute: Mapped[int] = mapped_column(Integer)
    stoppage_minute: Mapped[int | None] = mapped_column(Integer)
    team_side: Mapped[str] = mapped_column(String(4))
    event_utc: Mapped[datetime | None]


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (
        UniqueConstraint("fixture_id", "source", "odds_type", "bookmaker", "available_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[str] = mapped_column(ForeignKey("fixtures.id"), index=True)
    source: Mapped[str] = mapped_column(String(64))
    odds_type: Mapped[str] = mapped_column(String(16))  # prematch | closing
    bookmaker: Mapped[str] = mapped_column(String(32))
    available_at: Mapped[datetime]
    # How available_at is known: "observed" (we downloaded these prices at that time),
    # "inferred" (from the provider's published collection schedule; historical files),
    # or "kickoff" (closing prices, available at kick-off). See DATA_DICTIONARY.md.
    availability_basis: Mapped[str] = mapped_column(String(16), default="inferred",
                                                    server_default="inferred")
    home: Mapped[float] = mapped_column(Float)
    draw: Mapped[float] = mapped_column(Float)
    away: Mapped[float] = mapped_column(Float)
    over25: Mapped[float | None] = mapped_column(Float)
    under25: Mapped[float | None] = mapped_column(Float)
    source_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("source_snapshots.id"))


class FixtureStat(Base):
    __tablename__ = "fixture_stats"
    __table_args__ = (UniqueConstraint("fixture_id", "source"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[str] = mapped_column(ForeignKey("fixtures.id"), index=True)
    source: Mapped[str] = mapped_column(String(64))
    available_at: Mapped[datetime]
    home_shots: Mapped[float | None] = mapped_column(Float)
    away_shots: Mapped[float | None] = mapped_column(Float)
    home_shots_on_target: Mapped[float | None] = mapped_column(Float)
    away_shots_on_target: Mapped[float | None] = mapped_column(Float)
    home_xg: Mapped[float | None] = mapped_column(Float)
    away_xg: Mapped[float | None] = mapped_column(Float)
    source_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("source_snapshots.id"))


# --------------------------------------------------------------------------- models
class FeatureSnapshot(Base):
    __tablename__ = "feature_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    cutoff_at: Mapped[datetime]
    schema_version: Mapped[int] = mapped_column(Integer)
    n_results: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class ModelRegistryEntry(Base):
    __tablename__ = "model_registry"
    __table_args__ = (
        UniqueConstraint("model_name", "code_version", "config_hash", "training_fingerprint"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(64))
    code_version: Mapped[str] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict[str, Any]]
    training_cutoff: Mapped[datetime]
    training_fingerprint: Mapped[str] = mapped_column(String(64))
    summary: Mapped[dict[str, Any]]
    artifact_uri: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(default=_now)


# --------------------------------------------------------------------------- rounds & runs
class Super6Round(Base):
    __tablename__ = "super6_rounds"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    deadline_utc: Mapped[datetime]
    rules_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    illustrative: Mapped[bool] = mapped_column(Boolean)
    source_note: Mapped[str | None] = mapped_column(String(512))
    spec: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(default=_now)
    fixtures: Mapped[list[Super6RoundFixture]] = relationship(
        back_populates="round", order_by="Super6RoundFixture.display_order"
    )


class Super6RoundFixture(Base):
    __tablename__ = "super6_round_fixtures"
    __table_args__ = (UniqueConstraint("round_id", "display_order"),)
    round_id: Mapped[str] = mapped_column(ForeignKey("super6_rounds.id"), primary_key=True)
    fixture_id: Mapped[str] = mapped_column(ForeignKey("fixtures.id"), primary_key=True)
    display_order: Mapped[int] = mapped_column(Integer)
    round: Mapped[Super6Round] = relationship(back_populates="fixtures")


class PredictionRun(Base):
    __tablename__ = "prediction_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    round_id: Mapped[str] = mapped_column(ForeignKey("super6_rounds.id"), index=True)
    model_registry_id: Mapped[int] = mapped_column(ForeignKey("model_registry.id"))
    cutoff_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(default=_now)
    status: Mapped[str] = mapped_column(String(16))
    code_version: Mapped[str] = mapped_column(String(64))
    rules_version: Mapped[str] = mapped_column(String(64))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict[str, Any]]
    warnings: Mapped[list[Any]]
    block_reasons: Mapped[list[Any]]
    published_at: Mapped[datetime | None]
    locked_at: Mapped[datetime | None]
    settled_at: Mapped[datetime | None]
    supersedes_run_id: Mapped[str | None] = mapped_column(ForeignKey("prediction_runs.id"))
    correction_reason: Mapped[str | None] = mapped_column(String(512))


class FixtureForecast(Base):
    __tablename__ = "fixture_forecasts"
    run_id: Mapped[str] = mapped_column(ForeignKey("prediction_runs.id"), primary_key=True)
    fixture_id: Mapped[str] = mapped_column(ForeignKey("fixtures.id"), primary_key=True)
    display_order: Mapped[int] = mapped_column(Integer)
    lam_home: Mapped[float] = mapped_column(Float)
    lam_away: Mapped[float] = mapped_column(Float)
    p_home: Mapped[float] = mapped_column(Float)
    p_draw: Mapped[float] = mapped_column(Float)
    p_away: Mapped[float] = mapped_column(Float)
    tail_probability: Mapped[float] = mapped_column(Float)
    grid_max_goals: Mapped[int] = mapped_column(Integer)
    warnings: Mapped[list[Any]]
    details: Mapped[dict[str, Any]]


class ScoreProbability(Base):
    __tablename__ = "score_probabilities"
    run_id: Mapped[str] = mapped_column(ForeignKey("prediction_runs.id"), primary_key=True)
    fixture_id: Mapped[str] = mapped_column(ForeignKey("fixtures.id"), primary_key=True)
    home_goals: Mapped[int] = mapped_column(Integer, primary_key=True)
    away_goals: Mapped[int] = mapped_column(Integer, primary_key=True)
    probability: Mapped[float] = mapped_column(Float)


class Recommendation(Base):
    __tablename__ = "recommendations"
    run_id: Mapped[str] = mapped_column(ForeignKey("prediction_runs.id"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), primary_key=True)
    fixture_id: Mapped[str] = mapped_column(ForeignKey("fixtures.id"), primary_key=True)
    home_goals: Mapped[int] = mapped_column(Integer)
    away_goals: Mapped[int] = mapped_column(Integer)
    exact_probability: Mapped[float] = mapped_column(Float)
    result_probability: Mapped[float] = mapped_column(Float)
    expected_points: Mapped[float] = mapped_column(Float)
    alternatives: Mapped[list[Any]]


class GoldenGoalPrediction(Base):
    __tablename__ = "golden_goal_predictions"
    run_id: Mapped[str] = mapped_column(ForeignKey("prediction_runs.id"), primary_key=True)
    minute: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(64))
    expected_abs_error: Mapped[float] = mapped_column(Float)
    interval_low: Mapped[int] = mapped_column(Integer)
    interval_high: Mapped[int] = mapped_column(Integer)
    p_no_goal: Mapped[float] = mapped_column(Float)
    details: Mapped[dict[str, Any]]


class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (UniqueConstraint("run_id", "mode", "metric"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("prediction_runs.id"), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    metric: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)
    evaluated_at: Mapped[datetime] = mapped_column(default=_now)


RUN_BODY_TYPES = (FixtureForecast, ScoreProbability, Recommendation, GoldenGoalPrediction)
_SETTLEMENT_FIELDS = {"status", "settled_at"}


def _run_status(session: Session, run_id: str) -> str | None:
    with session.no_autoflush:
        run = session.get(PredictionRun, run_id)
    if run is None:
        return None
    hist = inspect(run).attrs.status.history
    # Use the committed status: a run being locked in this flush may still be written.
    return str(hist.deleted[0]) if hist.deleted else run.status


@event.listens_for(Session, "before_flush")
def _guard_locked_runs(session: Session, flush_context: Any, instances: Any) -> None:
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, PredictionRun):
            state = inspect(obj)
            status_hist = state.attrs.status.history
            old_status = status_hist.deleted[0] if status_hist.deleted else obj.status
            if old_status not in FROZEN_STATUSES:
                continue
            if obj in session.deleted:
                raise ImmutableRecordError(f"run {obj.id} is {old_status}; it cannot be deleted")
            changed = {a.key for a in state.attrs if a.history.has_changes()}
            if changed - _SETTLEMENT_FIELDS or (
                "status" in changed
                and not (old_status == RunStatus.LOCKED and obj.status == RunStatus.SETTLED)
            ):
                raise ImmutableRecordError(
                    f"run {obj.id} is {old_status}; refusing to change {sorted(changed)}"
                )
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(obj, RUN_BODY_TYPES):
            if obj in session.dirty and not session.is_modified(obj):
                continue
            status = _run_status(session, obj.run_id)
            if status in FROZEN_STATUSES:
                raise ImmutableRecordError(
                    f"run {obj.run_id} is {status}; its {type(obj).__tablename__} are immutable"
                )
