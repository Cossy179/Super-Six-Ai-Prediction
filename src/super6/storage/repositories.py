"""Database reads and writes. Ingestion is idempotent; audit records are never edited."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from super6.domain import FixtureStatus
from super6.features.point_in_time import (
    MATCH_COLUMNS,
    ODDS_COLUMNS,
    STAT_COLUMNS,
    MatchHistory,
)
from super6.ingestion.adapters.football_data_couk import ParsedFile, RawFile
from super6.ingestion.canonicalize import TeamRegistry, normalise_name
from super6.storage.models import (
    ALLOWED_TRANSITIONS,
    Competition,
    DataQualityIssue,
    FeatureSnapshot,
    Fixture,
    FixtureResult,
    FixtureStat,
    GoalEvent,
    MarketSnapshot,
    ModelRegistryEntry,
    PredictionRun,
    RunStatus,
    SourceSnapshot,
    Team,
    TeamAlias,
)

_CHUNK = 500


def _chunks(items: list[Any], size: int = _CHUNK) -> Iterable[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


# --------------------------------------------------------------------------- reference data
def sync_competitions(session: Session, leagues: Mapping[str, Mapping[str, Any]]) -> None:
    existing = {c.id: c for c in session.scalars(select(Competition))}
    for code, info in leagues.items():
        comp = existing.get(code)
        if comp is None:
            session.add(Competition(id=code, name=info["name"], country=info["country"],
                                    pool=info["pool"], tier=int(info["tier"])))
        else:
            comp.name, comp.country = info["name"], info["country"]
            comp.pool, comp.tier = info["pool"], int(info["tier"])
    session.flush()


def sync_teams(session: Session, teams: Mapping[str, str], source: str,
               display_names: Mapping[str, str] | None = None) -> None:
    """Ensure teams exist and their source names are recorded as aliases."""
    known = set(session.scalars(select(Team.id)))
    aliases = {(a.source, a.normalised) for a in session.scalars(select(TeamAlias))}
    for team_id, source_name in teams.items():
        if team_id not in known:
            display = (display_names or {}).get(team_id, source_name)
            session.add(Team(id=team_id, canonical_name=display))
            known.add(team_id)
        key = (source, normalise_name(source_name))
        if key not in aliases:
            session.add(TeamAlias(team_id=team_id, source=source, alias=source_name,
                                  normalised=key[1]))
            aliases.add(key)
    session.flush()


def load_registry(session: Session, base: TeamRegistry | None = None) -> TeamRegistry:
    """Config aliases plus every team and alias already in the database."""
    reg = base or TeamRegistry.from_config()
    for team in session.scalars(select(Team)):
        if team.id not in reg.display_names:
            reg.add_team(team.id, team.canonical_name)
    for alias in session.scalars(select(TeamAlias)):
        if normalise_name(alias.alias) not in reg.aliases():
            reg.add_team(alias.team_id, reg.name(alias.team_id), [alias.alias])
    return reg


# --------------------------------------------------------------------------- ingestion
@dataclass
class IngestStats:
    uri: str
    skipped: bool = False
    fixtures_new: int = 0
    fixtures_updated: int = 0
    results_new: int = 0
    result_conflicts: int = 0
    odds_new: int = 0
    stats_new: int = 0
    issues: int = 0


def ingest_parsed(session: Session, raw: RawFile, parsed: ParsedFile, licence_id: str
                  ) -> IngestStats:
    stats = IngestStats(raw.uri)
    existing_snapshot = session.scalar(
        select(SourceSnapshot).where(SourceSnapshot.source == raw.source,
                                     SourceSnapshot.uri == raw.uri,
                                     SourceSnapshot.checksum == raw.checksum)
    )
    if existing_snapshot is not None:
        stats.skipped = True
        return stats
    report = parsed.report
    assert report is not None
    snapshot = SourceSnapshot(
        source=raw.source, uri=raw.uri, retrieved_at=raw.retrieved_at, checksum=raw.checksum,
        licence_id=licence_id, local_path=str(raw.local_path), rows_read=report.rows_read,
        rows_accepted=report.rows_accepted, quality_status=report.status,
    )
    session.add(snapshot)
    session.flush()
    for issue in report.issues:
        session.add(DataQualityIssue(source_snapshot_id=snapshot.id, severity=issue.severity,
                                     code=issue.code, detail=issue.detail[:1024],
                                     record_ref=issue.record_ref[:512]))
    stats.issues = len(report.issues)
    sync_teams(session, parsed.teams, raw.source, display_names=parsed.display_names)

    ids = [m["fixture_id"] for m in parsed.matches]
    existing: dict[str, Fixture] = {}
    for chunk in _chunks(ids):
        for row in session.scalars(select(Fixture).where(Fixture.id.in_(chunk))):
            existing[row.id] = row
    new_rows: list[dict[str, Any]] = []
    for m in parsed.matches:
        fx = existing.get(m["fixture_id"])
        if fx is None:
            new_rows.append({
                "id": m["fixture_id"], "competition_id": m["competition"], "season": m["season"],
                "home_team_id": m["home_team"], "away_team_id": m["away_team"],
                "kickoff_utc": m["kickoff_utc"], "kickoff_time_known": m["kickoff_time_known"],
                "neutral": False, "status": m["status"], "source": raw.source,
            })
        elif fx.status == FixtureStatus.SCHEDULED.value and (
            fx.kickoff_utc != m["kickoff_utc"] or fx.status != m["status"]
            or fx.source != raw.source
        ):
            # A provider listing (re)confirms the fixture, including one first created
            # from a round file.
            fx.kickoff_utc = m["kickoff_utc"]
            fx.kickoff_time_known = m["kickoff_time_known"]
            fx.status = m["status"]
            fx.source = raw.source
            stats.fixtures_updated += 1
    for chunk in _chunks(new_rows):
        session.execute(insert(Fixture), chunk)
    stats.fixtures_new = len(new_rows)

    finished = [m for m in parsed.matches if m["status"] == FixtureStatus.FINISHED.value]
    known_results: dict[str, tuple[int, int]] = {}
    for chunk in _chunks([m["fixture_id"] for m in finished]):
        for r in session.scalars(select(FixtureResult).where(
                FixtureResult.fixture_id.in_(chunk), FixtureResult.source == raw.source)):
            known_results[r.fixture_id] = (r.home_goals, r.away_goals)
    result_rows = []
    for m in finished:
        prior = known_results.get(m["fixture_id"])
        if prior is None:
            result_rows.append({
                "fixture_id": m["fixture_id"], "source": raw.source,
                "home_goals": m["home_goals"], "away_goals": m["away_goals"],
                "result_available_at": m["result_available_at"],
                "source_snapshot_id": snapshot.id,
            })
        elif prior != (m["home_goals"], m["away_goals"]):
            stats.result_conflicts += 1
            session.add(DataQualityIssue(
                source_snapshot_id=snapshot.id, severity="quarantine", code="RESULT_CONFLICT",
                detail=f"stored {prior[0]}-{prior[1]}, new file says "
                       f"{m['home_goals']}-{m['away_goals']}; original kept",
                record_ref=m["source_ref"][:512]))
    for chunk in _chunks(result_rows):
        session.execute(insert(FixtureResult), chunk)
    stats.results_new = len(result_rows)

    odds_keys: set[tuple[str, str, str, datetime]] = set()
    for chunk in _chunks(ids):
        for snap in session.scalars(select(MarketSnapshot).where(
                MarketSnapshot.fixture_id.in_(chunk), MarketSnapshot.source == raw.source)):
            odds_keys.add((snap.fixture_id, snap.odds_type, snap.bookmaker, snap.available_at))
    odds_rows = []
    for o in parsed.odds:
        key = (o["fixture_id"], o["odds_type"], o["bookmaker"],
               o["available_at"].astimezone(UTC))
        if key not in odds_keys:
            odds_keys.add(key)
            odds_rows.append({**o, "source": raw.source, "source_snapshot_id": snapshot.id})
    for chunk in _chunks(odds_rows):
        session.execute(insert(MarketSnapshot), chunk)
    stats.odds_new = len(odds_rows)

    have_stats: set[str] = set()
    for chunk in _chunks([s["fixture_id"] for s in parsed.stats]):
        have_stats.update(session.scalars(select(FixtureStat.fixture_id).where(
            FixtureStat.fixture_id.in_(chunk), FixtureStat.source == raw.source)))
    stat_rows = [{**s, "source": raw.source, "source_snapshot_id": snapshot.id}
                 for s in parsed.stats if s["fixture_id"] not in have_stats]
    for chunk in _chunks(stat_rows):
        session.execute(insert(FixtureStat), chunk)
    stats.stats_new = len(stat_rows)
    session.flush()
    return stats


# --------------------------------------------------------------------------- history
def load_history(session: Session, competitions: Iterable[str] | None = None) -> MatchHistory:
    stmt = (
        select(Fixture.id, Fixture.competition_id, Fixture.season, Fixture.kickoff_utc,
               Fixture.kickoff_time_known, Fixture.home_team_id, Fixture.away_team_id,
               Fixture.status, FixtureResult.home_goals, FixtureResult.away_goals,
               FixtureResult.result_available_at)
        .outerjoin(FixtureResult, FixtureResult.fixture_id == Fixture.id)
        .order_by(Fixture.kickoff_utc, Fixture.id, FixtureResult.recorded_at)
    )
    comps = list(competitions) if competitions is not None else None
    if comps is not None:
        stmt = stmt.where(Fixture.competition_id.in_(comps))
    rows = session.execute(stmt).all()
    matches = pd.DataFrame(rows, columns=MATCH_COLUMNS)
    matches = matches.drop_duplicates("fixture_id", keep="first")
    sstmt = select(FixtureStat.fixture_id, FixtureStat.home_shots, FixtureStat.away_shots,
                   FixtureStat.home_shots_on_target, FixtureStat.away_shots_on_target,
                   FixtureStat.home_xg, FixtureStat.away_xg).order_by(FixtureStat.id)
    stats = pd.DataFrame(session.execute(sstmt).all(), columns=["fixture_id", *STAT_COLUMNS])
    stats = stats.drop_duplicates("fixture_id", keep="first")
    matches = matches.merge(stats, on="fixture_id", how="left")
    meta = {fid: (neutral, source) for fid, neutral, source in
            session.execute(select(Fixture.id, Fixture.neutral, Fixture.source)).all()}
    matches["neutral"] = matches["fixture_id"].map(lambda f: meta.get(f, (False, ""))[0])
    matches["fixture_source"] = matches["fixture_id"].map(lambda f: meta.get(f, (False, ""))[1])
    # A FINISHED status without a stored result is not usable as a result.
    no_result = matches["home_goals"].isna() & (matches["status"] == FixtureStatus.FINISHED)
    matches.loc[no_result, "status"] = FixtureStatus.SCHEDULED.value

    ostmt = select(MarketSnapshot.fixture_id, MarketSnapshot.odds_type, MarketSnapshot.source,
                   MarketSnapshot.bookmaker, MarketSnapshot.available_at, MarketSnapshot.home,
                   MarketSnapshot.draw, MarketSnapshot.away, MarketSnapshot.over25,
                   MarketSnapshot.under25, MarketSnapshot.availability_basis)
    if comps is not None:
        ostmt = ostmt.join(Fixture, Fixture.id == MarketSnapshot.fixture_id).where(
            Fixture.competition_id.in_(comps))
    orows = session.execute(ostmt).all()
    odds = pd.DataFrame(orows, columns=["fixture_id", "odds_type", "source", "bookmaker",
                                        "available_at", "home", "draw", "away", "over25",
                                        "under25", "availability_basis"])
    odds["source"] = odds["source"] + ":" + odds["bookmaker"]
    odds = odds[[*ODDS_COLUMNS, "availability_basis"]]
    for col in ("over25", "under25"):
        odds[col] = pd.to_numeric(odds[col], errors="coerce")
    goals = load_goal_events(session)
    goals = goals.loc[goals["fixture_id"].isin(matches["fixture_id"])]
    return MatchHistory(matches, odds, goals)


# --------------------------------------------------------------------------- models & runs
def register_feature_snapshot(session: Session, fingerprint: str, cutoff: datetime,
                              n_results: int, schema_version: int = 1) -> FeatureSnapshot:
    snap = session.scalar(select(FeatureSnapshot).where(FeatureSnapshot.fingerprint ==
                                                        fingerprint))
    if snap is None:
        snap = FeatureSnapshot(fingerprint=fingerprint, cutoff_at=cutoff,
                               schema_version=schema_version, n_results=n_results)
        session.add(snap)
        session.flush()
    return snap


def register_model(session: Session, *, model_name: str, code_version: str,
                   config: Mapping[str, Any], training_cutoff: datetime,
                   training_fingerprint: str, summary: Mapping[str, Any]) -> ModelRegistryEntry:
    config_hash = stable_hash(config)
    entry = session.scalar(select(ModelRegistryEntry).where(
        ModelRegistryEntry.model_name == model_name,
        ModelRegistryEntry.code_version == code_version,
        ModelRegistryEntry.config_hash == config_hash,
        ModelRegistryEntry.training_fingerprint == training_fingerprint))
    if entry is None:
        entry = ModelRegistryEntry(
            model_name=model_name, code_version=code_version, config_hash=config_hash,
            config=json.loads(json.dumps(config, default=str)), training_cutoff=training_cutoff,
            training_fingerprint=training_fingerprint,
            summary=json.loads(json.dumps(summary, default=str)))
        session.add(entry)
        session.flush()
    return entry


class InvalidTransitionError(ValueError):
    pass


def transition_run(session: Session, run: PredictionRun, new_status: RunStatus,
                   now: datetime | None = None) -> None:
    current = RunStatus(run.status)
    if new_status not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(f"run {run.id}: {current} → {new_status} is not allowed")
    now = now or datetime.now(UTC)
    run.status = new_status.value
    if new_status is RunStatus.PUBLISHED:
        run.published_at = now
    elif new_status is RunStatus.LOCKED:
        run.locked_at = now
    elif new_status is RunStatus.SETTLED:
        run.settled_at = now
    session.flush()


# --------------------------------------------------------------------------- goal events
@dataclass
class GoalIngestStats:
    uri: str
    skipped: bool = False
    matches: int = 0
    goals_new: int = 0
    issues: int = 0


def ingest_goal_events(session: Session, *, source: str, uri: str, text: str, checksum: str,
                       retrieved_at: datetime, local_path: str | None, competition: str,
                       season_label: str, registry: TeamRegistry, licence_id: str
                       ) -> GoalIngestStats:
    """Store goal times from an openfootball league file (idempotent per file version).

    A match is rejected if its goals don't add up to its score, if it can't be matched to a
    canonical fixture, or if its score disagrees with the stored result.
    """
    from super6.ingestion.adapters.openfootball import fixture_id_for, parse_league_text
    from super6.ingestion.canonicalize import UnknownTeamError

    stats = GoalIngestStats(uri)
    if session.scalar(select(SourceSnapshot).where(
            SourceSnapshot.source == source, SourceSnapshot.uri == uri,
            SourceSnapshot.checksum == checksum)) is not None:
        stats.skipped = True
        return stats
    parsed = parse_league_text(text, int(season_label[:4]))
    snapshot = SourceSnapshot(source=source, uri=uri, retrieved_at=retrieved_at,
                              checksum=checksum, licence_id=licence_id, local_path=local_path,
                              rows_read=len(parsed.matches) + len(parsed.problems),
                              rows_accepted=0, quality_status="ok")
    session.add(snapshot)
    session.flush()

    def issue(code: str, detail: str) -> None:
        session.add(DataQualityIssue(source_snapshot_id=snapshot.id, severity="quarantine",
                                     code=code, detail=detail[:1024], record_ref=uri[:512]))
        stats.issues += 1

    for problem in parsed.problems:
        issue("GOALS_DONT_MATCH_SCORE", problem)
    have = set(session.scalars(select(GoalEvent.fixture_id).where(GoalEvent.source == source)))
    for match in parsed.matches:
        try:
            fid = fixture_id_for(competition, match, registry)
        except UnknownTeamError as exc:
            issue("GOAL_TEAM_UNKNOWN", str(exc.args[0]))
            continue
        result = session.scalar(select(FixtureResult).where(FixtureResult.fixture_id == fid))
        if session.get(Fixture, fid) is None:
            issue("GOAL_FIXTURE_NOT_FOUND", f"{fid} not in fixtures")
            continue
        if result is not None and (result.home_goals, result.away_goals) != (
                match.home_goals, match.away_goals):
            issue("GOAL_SCORE_CONFLICT", f"{fid}: results say {result.home_goals}-"
                  f"{result.away_goals}, goal list says {match.home_goals}-{match.away_goals}")
            continue
        stats.matches += 1
        if fid in have:
            continue
        for g in match.goals:
            session.add(GoalEvent(fixture_id=fid, source=source, period=g.period,
                                  match_minute=g.minute, stoppage_minute=g.stoppage,
                                  team_side=g.side, event_utc=None))
            stats.goals_new += 1
        have.add(fid)
    snapshot.rows_accepted = stats.matches
    snapshot.quality_status = "issues" if stats.issues else "ok"
    session.flush()
    return stats


def load_goal_events(session: Session, source: str | None = None) -> pd.DataFrame:
    stmt = select(GoalEvent.fixture_id, GoalEvent.source, GoalEvent.period,
                  GoalEvent.match_minute, GoalEvent.stoppage_minute, GoalEvent.team_side)
    if source is not None:
        stmt = stmt.where(GoalEvent.source == source)
    return pd.DataFrame(session.execute(stmt).all(),
                        columns=["fixture_id", "source", "period", "match_minute",
                                 "stoppage_minute", "team_side"])
