"""The unattended round workflow (DESIGN.md §16.1): run ``super6 cycle`` hourly.

Each run is idempotent and does whatever the clock says is due:

1. **Refresh data.** Current-season result files at most every ``refresh_hours``, and the
   provider's upcoming fixtures and odds at most every ``fixtures_refresh_minutes``.
2. **Find the round.** If no round file covers the coming weekend, fetch it from public
   previews (``round_sources``).
3. **Before the deadline**:
   - align kick-offs with the provider's listing, rewriting the round file and alerting
     if a game has moved;
   - re-forecast, and save and publish a new run only if the forecast changed;
   - inside the lock window (``lock_window_minutes`` before the deadline), lock the
     latest run once.
4. **After the deadline**:
   - alert if no run was locked;
   - settle once results are in. A fixture still without a result ``void_after_days``
     after kick-off is treated as void (postponed or abandoned, per the rules).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from super6.domain import FixtureStatus
from super6.features.point_in_time import MatchHistory
from super6.ingestion.canonicalize import TeamRegistry
from super6.ingestion.round_import import (
    RoundImportError,
    import_round,
    next_saturday,
    parse_fixture_text,
)
from super6.ingestion.round_sources import SourceReport
from super6.jobs.alerts import AlertSink
from super6.models.base import ScoreModel
from super6.prediction.optimiser import Objective
from super6.prediction.round_spec import RoundSpec, load_round, resolve_round
from super6.prediction.service import (
    LockError,
    RoundForecast,
    equivalent_saved_run,
    forecast_round,
    persist_forecast,
    settle_round,
)
from super6.rules import CompetitionRules
from super6.storage.db import session_scope
from super6.storage.models import (
    PredictionRun,
    RunStatus,
    Super6Round,
)
from super6.storage.repositories import load_registry
from super6.timeutil import UK, iso


@dataclass(frozen=True)
class CycleConfig:
    lock_window_minutes: float = 60.0
    void_after_days: float = 3.0
    settle_after_hours: float = 3.0
    refresh_hours: float = 6.0
    fixtures_refresh_minutes: float = 55.0
    lookback_days: float = 14.0
    fetch_rounds: bool = True
    fetch_within_hours: float = 72.0  # only look for a missing round this close to it


@dataclass
class CycleDeps:
    refresh: Callable[[Session, datetime], list[str]]
    load_history: Callable[[Session], MatchHistory]
    fetch_round_lines: Callable[[date], SourceReport]
    model_factory: Callable[[], ScoreModel]
    rounds_dir: Path
    known_competitions: set[str]
    golden_goal_config: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class RoundOutcome:
    round_id: str
    deadline_utc: str
    actions: list[str] = field(default_factory=list)
    run_id: str | None = None
    status: str | None = None
    picks: list[str] = field(default_factory=list)


@dataclass
class CycleReport:
    at: str
    refresh: list[str] = field(default_factory=list)
    rounds: list[RoundOutcome] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers
def _round_files(rounds_dir: Path, alerts: AlertSink) -> list[tuple[Path, RoundSpec]]:
    out = []
    for path in sorted(rounds_dir.glob("*.yaml")):
        try:
            spec = load_round(path)
        except Exception as exc:  # a bad file must not stop other rounds
            alerts.emit("error", "ROUND_FILE_INVALID", f"{path.name}: {exc}")
            continue
        if not spec.illustrative:
            out.append((path, spec))
    return out


def _covers_weekend(spec: RoundSpec, saturday: date) -> bool:
    days = {f.kickoff_utc.astimezone(UK).date() for f in spec.fixtures}
    return any(saturday - timedelta(days=1) <= d <= saturday + timedelta(days=3) for d in days)


def _write_spec(path: Path, spec_dict: dict[str, Any], header: str) -> None:
    path.write_text(header + yaml.safe_dump(spec_dict, sort_keys=False), encoding="utf-8")


def _summary(fc: RoundForecast, registry: TeamRegistry) -> list[str]:
    picks = fc.picks[Objective.POINTS]
    lines = []
    for fx, fp in zip(fc.fixtures, picks.fixtures, strict=True):
        lines.append(f"{registry.name(fx.home_team)} {fp.pick.home_goals}-{fp.pick.away_goals} "
                     f"{registry.name(fx.away_team)}")
    if fc.golden_goal is not None:
        lines.append(f"Golden Goal: {fc.golden_goal.minute}")
    return lines


def _align_kickoffs(path: Path, spec: RoundSpec, history: MatchHistory, registry: TeamRegistry,
                    alerts: AlertSink) -> RoundSpec:
    """Adopt the provider's kick-off times for the round's fixtures, alerting on changes."""
    m = history.matches
    listed = m.loc[(m["status"] == FixtureStatus.SCHEDULED.value)]
    if "fixture_source" in listed.columns:
        listed = listed.loc[listed["fixture_source"] != "round_file"]
    data = spec.model_dump(mode="json")
    changed = []
    for fx in data["fixtures"]:
        try:
            home, away = registry.resolve(fx["home"]), registry.resolve(fx["away"])
        except KeyError:
            continue
        ko = pd.Timestamp(fx["kickoff_utc"])
        near = listed.loc[(listed["home_team"] == home) & (listed["away_team"] == away)
                          & ((listed["kickoff_utc"] - ko).abs() <= pd.Timedelta(days=4))]
        if len(near) == 1 and near["kickoff_utc"].iloc[0] != ko:
            new = near["kickoff_utc"].iloc[0]
            changed.append(f"{fx['home']} v {fx['away']}: {ko.tz_convert(UK):%a %H:%M} → "
                           f"{new.tz_convert(UK):%a %d %b %H:%M} UK")
            fx["kickoff_utc"] = iso(new)
    if not changed:
        return spec
    first = min(pd.Timestamp(f["kickoff_utc"]) for f in data["fixtures"])
    if pd.Timestamp(data["deadline_utc"]) != first:
        data["deadline_utc"] = iso(first)
        changed.append(f"deadline now {first.tz_convert(UK):%a %d %b %H:%M} UK (first kick-off)")
    _write_spec(path, data, "# Kick-off times updated from the provider by `super6 cycle`.\n")
    alerts.emit("warning", "KICKOFF_CHANGED", "; ".join(changed), spec.round_id, dedupe=False)
    return RoundSpec.model_validate(data)


# --------------------------------------------------------------------------- the cycle
def run_cycle(engine: Engine, rules: CompetitionRules, deps: CycleDeps, alerts: AlertSink,
              now: datetime | None = None, config: CycleConfig | None = None) -> CycleReport:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    cfg = config or CycleConfig()
    report = CycleReport(at=iso(now))
    with session_scope(engine) as session:
        try:
            report.refresh = deps.refresh(session, now)
        except Exception as exc:  # stale data is flagged, not fatal
            alerts.emit("warning", "REFRESH_FAILED", f"data refresh failed: {exc}")
    with session_scope(engine) as session:
        history = deps.load_history(session)
        registry = load_registry(session, TeamRegistry.from_config())

    files = _round_files(deps.rounds_dir, alerts)
    saturday = next_saturday(now.astimezone(UK).date())
    hours_to_saturday = (datetime(saturday.year, saturday.month, saturday.day, 15,
                                  tzinfo=UK) - now).total_seconds() / 3600
    if (cfg.fetch_rounds and 0 < hours_to_saturday <= cfg.fetch_within_hours
            and not any(_covers_weekend(spec, saturday) for _, spec in files)):
        files.extend(_fetch_round(saturday, deps, history, registry, rules, alerts))

    for path, spec in files:
        deadline = spec.deadline_utc.astimezone(UTC)
        try:
            if deadline > now:
                report.rounds.append(_before_deadline(engine, path, spec, history, registry,
                                                      rules, deps, alerts, now, cfg))
            elif now - deadline <= timedelta(days=cfg.lookback_days):
                report.rounds.append(_after_deadline(engine, spec, rules, alerts, now, cfg))
        except Exception as exc:  # one failing round must not stop the others
            alerts.emit("error", "ROUND_CYCLE_FAILED", f"{type(exc).__name__}: {exc}",
                        spec.round_id)
            report.rounds.append(RoundOutcome(spec.round_id, iso(deadline), ["failed"]))
    return report


# --------------------------------------------------------------------------- production wiring
def provider_refresh(data_dir: Path, cfg: CycleConfig, competitions: list[str]
                     ) -> Callable[[Session, datetime], list[str]]:
    """Refresh current-season results and upcoming fixtures/odds, rate-limited by file age."""
    import time

    from super6.ingestion import pipeline
    from super6.ingestion.adapters import football_data_couk as fdc

    raw = data_dir / "raw"

    def refresh(session: Session, now: datetime) -> list[str]:
        msgs: list[str] = []
        season = fdc.season_for_date(now.astimezone(UK).date())
        folder = raw / fdc.SOURCE / fdc.season_code(season)
        stale = [c for c in competitions
                 if not (folder / f"{c}.csv").exists()
                 or time.time() - (folder / f"{c}.csv").stat().st_mtime > cfg.refresh_hours * 3600]
        if stale:
            fdc.download_seasons(stale, [season], raw, refresh=True, log=msgs.append)
        new = [s for s in pipeline.ingest_raw_dir(session, raw, log=lambda _: None)
               if not s.skipped]
        msgs.append(f"ingested {len(new)} new result file version(s)")
        fixtures = sorted((raw / fdc.SOURCE / "fixtures").glob("fixtures-*.csv"))
        if not fixtures or time.time() - fixtures[-1].stat().st_mtime > \
                cfg.fixtures_refresh_minutes * 60:
            pipeline.ingest_upcoming(session, raw, log=msgs.append)
        return msgs

    return refresh


def _fetch_round(saturday: date, deps: CycleDeps, history: MatchHistory,
                 registry: TeamRegistry, rules: CompetitionRules, alerts: AlertSink
                 ) -> list[tuple[Path, RoundSpec]]:
    try:
        found = deps.fetch_round_lines(saturday)
    except Exception as exc:
        alerts.emit("warning", "ROUND_FETCH_FAILED", str(exc))
        return []
    if len(found.lines) != rules.fixtures_per_round:
        alerts.emit("warning", "ROUND_NOT_FOUND",
                    f"found {len(found.lines)} Super 6 matches for {saturday}; paste the six "
                    "from the app with `super6 round import`")
        return []
    try:
        imported = import_round(parse_fixture_text("\n".join(found.lines)), registry, history,
                                saturday, known_competitions=deps.known_competitions)
    except RoundImportError as exc:
        alerts.emit("error", "ROUND_IMPORT_FAILED", "; ".join(exc.problems))
        return []
    sources = ", ".join(a["url"] for a in found.articles if a["super6_matches"])
    path = deps.rounds_dir / f"{imported.round_id}.yaml"
    path.write_text(imported.to_yaml(rules.rules_version,
                                     f"Found by `super6 cycle` in: {sources}"),
                    encoding="utf-8")
    alerts.emit("info", "ROUND_FETCHED", f"{imported.round_id}: {', '.join(found.lines)}",
                imported.round_id)
    for w in imported.warnings:
        alerts.emit("warning", "ROUND_IMPORT_WARNING", w, imported.round_id)
    return [(path, load_round(path))]


def _before_deadline(engine: Engine, path: Path, spec: RoundSpec, history: MatchHistory,
                     registry: TeamRegistry, rules: CompetitionRules, deps: CycleDeps,
                     alerts: AlertSink, now: datetime, cfg: CycleConfig) -> RoundOutcome:
    spec = _align_kickoffs(path, spec, history, registry, alerts)
    out = RoundOutcome(spec.round_id, iso(spec.deadline_utc))
    resolved = resolve_round(spec, registry, rules, deps.known_competitions)
    if not resolved.fixtures:
        alerts.emit("error", "ROUND_BLOCKED", "; ".join(resolved.block_reasons), spec.round_id)
        out.actions.append("blocked")
        return out
    cutoff = min(now, spec.deadline_utc - timedelta(minutes=1))
    fc = forecast_round(history, resolved, cutoff, deps.model_factory(), rules,
                        deps.golden_goal_config)
    if fc.block_reasons:
        alerts.emit("error", "ROUND_BLOCKED", "; ".join(fc.block_reasons), spec.round_id)
    with session_scope(engine) as session:
        locked = session.scalar(select(PredictionRun).where(
            PredictionRun.round_id == spec.round_id,
            PredictionRun.status.in_([RunStatus.LOCKED.value, RunStatus.SETTLED.value])))
        if locked is not None:
            out.actions.append("already locked")
            out.run_id, out.status = locked.id, locked.status
            return out
        same = equivalent_saved_run(session, spec.round_id, fc,
                                    spec.model_dump(mode="json"))
        if same is not None:
            run = same
            out.actions.append("forecast unchanged")
        else:
            run = persist_forecast(session, resolved, fc, publish=True)
            out.actions.append("forecast saved and published")
        out.run_id, out.status = run.id, run.status
        out.picks = _summary(fc, registry)
        in_window = now >= spec.deadline_utc - timedelta(minutes=cfg.lock_window_minutes)
        if not spec.confirmed:
            alerts.emit("error" if in_window else "warning", "ROUND_UNCONFIRMED",
                        f"{spec.round_id}: fixtures were found automatically and are not "
                        f"confirmed, so the forecast will not be locked. Check them in the "
                        f"app, then `super6 round confirm {spec.round_id}` (or paste the "
                        "right six with `super6 round import`)", spec.round_id)
            out.actions.append("unconfirmed: not locking")
        elif in_window and run.status in (RunStatus.VALIDATED.value,
                                          RunStatus.PUBLISHED.value):
            from super6.prediction.service import lock_run

            try:
                lock_run(session, run.id, now)
                out.actions.append("locked")
                out.status = RunStatus.LOCKED.value
                alerts.emit("info", "ROUND_LOCKED",
                            f"{spec.round_id} locked ({run.id}): " + "; ".join(out.picks),
                            spec.round_id, dedupe=False)
            except LockError as exc:
                alerts.emit("error", "LOCK_FAILED", str(exc), spec.round_id)
    return out


def _after_deadline(engine: Engine, spec: RoundSpec, rules: CompetitionRules,
                    alerts: AlertSink, now: datetime, cfg: CycleConfig) -> RoundOutcome:
    out = RoundOutcome(spec.round_id, iso(spec.deadline_utc))
    with session_scope(engine) as session:
        rnd = session.get(Super6Round, spec.round_id)
        runs = session.scalars(select(PredictionRun).where(
            PredictionRun.round_id == spec.round_id)).all() if rnd else []
        locked = [r for r in runs if r.status in (RunStatus.LOCKED.value, RunStatus.SETTLED.value)]
        if not locked:
            alerts.emit("error", "ROUND_NOT_LOCKED",
                        f"deadline {iso(spec.deadline_utc)} passed with no locked forecast",
                        spec.round_id)
            out.actions.append("missed lock")
        else:
            out.run_id, out.status = locked[0].id, locked[0].status
        if rnd is None or rnd.status == "SETTLED":
            out.actions.append("settled" if rnd is not None else "no saved round")
            return out
        last_kickoff = max(f.kickoff_utc for f in spec.fixtures)
        if now < last_kickoff + timedelta(hours=cfg.settle_after_hours):
            out.actions.append("waiting for matches to finish")
            return out
        allow_missing = now >= last_kickoff + timedelta(days=cfg.void_after_days)
        results = settle_round(session, spec.round_id, rules, allow_missing=allow_missing)
        main = next((s for s in results if s.run_id == out.run_id), None)
        if main is None or (main.missing_fixtures and not main.metrics):
            missing = main.missing_fixtures if main else []
            out.actions.append(f"waiting for results ({len(missing)} missing)")
            return out
        m = main.metrics
        out.status = main.status
        out.actions.append("settled")
        if main.missing_fixtures:
            alerts.emit("warning", "VOID_FIXTURES",
                        f"no result {cfg.void_after_days:g} days after kick-off, treated as "
                        f"void: {', '.join(main.missing_fixtures)}", spec.round_id)
        alerts.emit("info", "ROUND_SETTLED",
                    f"{spec.round_id}: points mode {m['points']['points']:g} pts "
                    f"({m['points']['exact_hits']:g} exact), jackpot mode "
                    f"{m['jackpot']['points']:g} pts", spec.round_id)
    return out
