"""Command-line interface: ``super6 --help``."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import click
import pandas as pd
from sqlalchemy import Engine, func, select

from super6.config import Settings, code_version, project_root
from super6.ingestion import pipeline
from super6.ingestion.adapters import football_data_couk as fdc
from super6.ingestion.canonicalize import TeamRegistry
from super6.models import MODEL_CLASSES, build_model, load_model_defaults
from super6.prediction.round_spec import load_round, resolve_round
from super6.rules import RulesValidationError, load_rules
from super6.storage.db import make_engine, session_scope
from super6.timeutil import UK, ensure_utc, iso


def _engine() -> Engine:
    return make_engine(Settings.from_env().database_url)


def _pct(p: float) -> str:
    return f"{100 * p:.1f}%"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    cells = [[str(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h)
              for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True))
    sep = "  ".join("-" * w for w in widths)
    body = ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)) for r in cells]
    return "\n".join([line, sep, *body])


@click.group()
@click.version_option(code_version(), prog_name="super6")
def main() -> None:
    """Super 6 AI: probabilistic exact-score forecasts and Super 6 decision support.

    Predictions are estimates, never guarantees. Entries are submitted manually.
    """


# --------------------------------------------------------------------------- db
@main.group()
def db() -> None:
    """Database schema management."""


def upgrade_database(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(project_root() / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root() / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


@db.command("init")
def db_init() -> None:
    """Create or upgrade the schema (Alembic ``upgrade head``)."""
    url = Settings.from_env().database_url
    make_engine(url).dispose()  # creates the SQLite directory if needed
    upgrade_database(url)
    click.echo(f"schema up to date at {url}")


# --------------------------------------------------------------------------- data
@main.group()
def data() -> None:
    """Download, ingest and inspect source data."""


@data.command("download")
@click.option("--competitions", default=None, help="Comma-separated codes (default: leagues.yaml)")
@click.option("--first-season", default=None, help="e.g. 2016-17 (default: leagues.yaml)")
@click.option("--last-season", default=None, help="Default: the current season")
@click.option("--refresh-current/--no-refresh-current", default=True,
              help="Re-download the current season's files (they change weekly)")
def data_download(competitions: str | None, first_season: str | None, last_season: str | None,
                  refresh_current: bool) -> None:
    """Download Football-Data.co.uk season CSVs into data/raw (not committed to git)."""
    cfg = pipeline.leagues_config()["default_ingest"]
    comps = competitions.split(",") if competitions else list(cfg["competitions"])
    current = fdc.season_for_date(datetime.now(UK).date())
    seasons = fdc.seasons_between(first_season or cfg["first_season"], last_season or current)
    raw_dir = Settings.from_env().data_dir / "raw"
    older = [s for s in seasons if s != current]
    fdc.download_seasons(comps, older, raw_dir, log=click.echo)
    if current in seasons:
        fdc.download_seasons(comps, [current], raw_dir, refresh=refresh_current, log=click.echo)
    click.echo(f"raw files in {raw_dir}")


@data.command("ingest")
@click.option("--competitions", default=None)
def data_ingest(competitions: str | None) -> None:
    """Validate downloaded files and load them into the database (idempotent)."""
    settings = Settings.from_env()
    with session_scope(_engine()) as session:
        stats = pipeline.ingest_raw_dir(session, settings.data_dir / "raw",
                                        competitions.split(",") if competitions else None,
                                        log=click.echo)
    new = [s for s in stats if not s.skipped]
    click.echo(f"{len(new)} new file snapshots, {len(stats) - len(new)} unchanged")


@data.command("upcoming")
@click.option("--days", default=8, show_default=True)
@click.option("--competitions", default="E0,E1,E2,E3,SC0", show_default=True)
@click.option("--fetch/--no-fetch", default=True, help="Download fixtures.csv first")
def data_upcoming(days: int, competitions: str, fetch: bool) -> None:
    """List upcoming fixtures (to help write a round file)."""
    from super6.storage.models import Fixture, Team

    settings = Settings.from_env()
    with session_scope(_engine()) as session:
        if fetch:
            pipeline.ingest_upcoming(session, settings.data_dir / "raw", log=click.echo)
        now = datetime.now(UTC)
        names = dict(session.execute(select(Team.id, Team.canonical_name)).all())
        rows = session.scalars(select(Fixture).where(
            Fixture.kickoff_utc >= now, Fixture.kickoff_utc <= now + timedelta(days=days),
            Fixture.competition_id.in_(competitions.split(","))).order_by(
            Fixture.kickoff_utc, Fixture.competition_id)).all()
        click.echo(_table(["kickoff (UK)", "comp", "home", "away", "kickoff_utc", "source"], [
            [f.kickoff_utc.astimezone(UK).strftime("%a %d %b %H:%M"), f.competition_id,
             names.get(f.home_team_id, f.home_team_id), names.get(f.away_team_id, f.away_team_id),
             iso(f.kickoff_utc), f.source] for f in rows]))


@data.command("goals")
@click.option("--competitions", default="E0", show_default=True)
@click.option("--first-season", default="2025-26", show_default=True,
              help="openfootball has Premier League goal minutes from 2025-26")
def data_goals(competitions: str, first_season: str) -> None:
    """Download and store goal minutes from openfootball (CC0) for the Golden Goal model."""
    from super6.ingestion.adapters import openfootball as of
    from super6.ingestion.round_sources import PoliteFetcher
    from super6.storage.repositories import ingest_goal_events, load_registry

    settings = Settings.from_env()
    fetcher = PoliteFetcher(raw_dir=settings.data_dir / "raw" / of.SOURCE)
    current = fdc.season_for_date(datetime.now(UK).date())
    with session_scope(_engine()) as session:
        registry = load_registry(session, TeamRegistry.from_config())
        for comp in competitions.split(","):
            for season in fdc.seasons_between(first_season, current):
                url = of.season_path(comp, season)
                try:
                    page = fetcher.get(url)
                except Exception as exc:
                    click.echo(f"skipped {url}: {exc}")
                    continue
                st = ingest_goal_events(
                    session, source=of.SOURCE, uri=url, text=page.text, checksum=page.checksum,
                    retrieved_at=page.retrieved_at,
                    local_path=str(page.local_path) if page.local_path else None,
                    competition=comp, season_label=season, registry=registry,
                    licence_id="CC0-1.0")
                click.echo(f"{comp} {season}: " + ("unchanged" if st.skipped else
                           f"{st.matches} matches with goal times, +{st.goals_new} goals, "
                           f"{st.issues} issues"))


@data.command("status")
def data_status() -> None:
    """Coverage, freshness and data-quality summary."""
    from super6.storage.models import (
        DataQualityIssue,
        Fixture,
        FixtureResult,
        MarketSnapshot,
        SourceSnapshot,
    )

    with session_scope(_engine()) as session:
        rows = session.execute(
            select(Fixture.competition_id, Fixture.season, func.count(FixtureResult.id),
                   func.max(FixtureResult.result_available_at))
            .join(FixtureResult, FixtureResult.fixture_id == Fixture.id)
            .group_by(Fixture.competition_id, Fixture.season)
            .order_by(Fixture.competition_id, Fixture.season)).all()
        click.echo(_table(["comp", "season", "results", "latest result (UTC)"],
                          [[c, s, n, iso(t)] for c, s, n, t in rows]))
        odds = session.execute(
            select(MarketSnapshot.odds_type, MarketSnapshot.availability_basis, func.count(),
                   func.count(func.distinct(MarketSnapshot.fixture_id)))
            .group_by(MarketSnapshot.odds_type, MarketSnapshot.availability_basis)
            .order_by(MarketSnapshot.odds_type, MarketSnapshot.availability_basis)).all()
        click.echo("\nodds snapshots by how their timestamp is known (observed = downloaded by "
                   "us before kick-off; inferred = provider schedule):")
        click.echo(_table(["type", "timestamp basis", "snapshots", "fixtures"],
                          [list(r) for r in odds]))
        observed_played = session.scalar(
            select(func.count(func.distinct(MarketSnapshot.fixture_id)))
            .join(FixtureResult, FixtureResult.fixture_id == MarketSnapshot.fixture_id)
            .where(MarketSnapshot.availability_basis == "observed"))
        click.echo(f"finished fixtures with observed pre-match odds: {observed_played}")
        snaps = session.scalar(select(func.count(SourceSnapshot.id)))
        issues = session.execute(select(DataQualityIssue.severity, DataQualityIssue.code,
                                        func.count()).group_by(DataQualityIssue.severity,
                                                               DataQualityIssue.code)).all()
        click.echo(f"\n{snaps} source snapshots; quality issues: "
                   + (", ".join(f"{s}:{c}={n}" for s, c, n in issues) or "none"))


# --------------------------------------------------------------------------- predict
def _print_forecast(payload: dict[str, Any]) -> None:
    pts = {f["fixture_id"]: f for f in payload["modes"]["points"]["fixtures"]}
    jp = {f["fixture_id"]: f for f in payload["modes"]["jackpot"]["fixtures"]}
    rows = []
    for fx in payload["fixtures"]:
        p, j = pts[fx["fixture_id"]], jp[fx["fixture_id"]]
        o = fx["outcome_probabilities"]
        rows.append([
            f"{fx['home_team']} v {fx['away_team']}",
            f"{p['predicted_home_goals']}-{p['predicted_away_goals']}",
            f"{j['predicted_home_goals']}-{j['predicted_away_goals']}",
            _pct(j["exact_score_probability"]),
            f"{p['expected_points']:.2f}",
            f"{_pct(o['home'])}/{_pct(o['draw'])}/{_pct(o['away'])}",
            f"{fx['expected_goals']['home']:.2f}/{fx['expected_goals']['away']:.2f}",
            fx["forecast_basis"],
        ])
    click.echo(_table(["fixture", "points", "jackpot", "P(jackpot pick)", "E[pts]", "H/D/A",
                       "xG h/a", "basis"], rows))
    m = payload["modes"]
    click.echo(f"\nexpected points: points mode {m['points']['expected_points']:.2f}, "
               f"jackpot mode {m['jackpot']['expected_points']:.2f}")
    click.echo(f"P(all six jackpot picks exact) ≈ {m['jackpot']['joint_exact_probability']:.2e} "
               "(assumes independent matches)")
    gg = payload.get("golden_goal")
    if gg:
        lo, hi = gg["interval_80"]
        basis = {"uniform-hazard": "constant scoring rate",
                 "empirical-hazard": "goal-time profile from stored goal minutes"}.get(
            str(gg["method"]).removesuffix("-simulation"), "")
        click.echo(f"Golden Goal: minute {gg['minute']} (80% interval {lo}–{hi}, expected "
                   f"error ±{gg['expected_absolute_error_minutes']} min) [{gg['method']}"
                   + (f": {basis}]" if basis else "]"))
    no_odds = [f for f in payload["fixtures"] if f["forecast_basis"] == "results model"]
    if no_odds and payload["model_version"].startswith("ensemble@"):
        click.secho(f"\n{len(no_odds)} fixture(s) had no pre-deadline odds and use the results "
                    "model only, which backtests worse. Run `super6 data upcoming` once the "
                    "provider lists the fixtures, or add `odds:` to the round file.", fg="yellow")
    if payload["warnings"]:
        click.echo("\nwarnings:\n  " + "\n  ".join(payload["warnings"]))
    if payload["block_reasons"]:
        click.secho("\nBLOCKED:\n  " + "\n  ".join(payload["block_reasons"]), fg="red")


@main.command()
@click.argument("round_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--model", "model_name", default=None,
              type=click.Choice(sorted(MODEL_CLASSES)), help="Default: model_defaults.yaml")
@click.option("--cutoff", default=None,
              help="ISO UTC timestamp. Default: now, or one minute before the deadline")
@click.option("--publish", is_flag=True, help="Mark the run PUBLISHED if it validates")
@click.option("--json", "json_path", type=click.Path(dir_okay=False), default=None)
@click.option("--save/--no-save", default=True, help="Persist the run (default: yes)")
def predict(round_file: str, model_name: str | None, cutoff: str | None, publish: bool,
            json_path: str | None, save: bool) -> None:
    """Forecast a six-fixture round from a verified round file."""
    from super6.models.base import CutoffAfterKickoffError
    from super6.prediction.service import forecast_round, forecast_to_json, persist_forecast
    from super6.storage.repositories import load_history, load_registry

    try:
        rules = load_rules()
    except RulesValidationError as exc:
        raise click.ClickException(str(exc)) from exc
    spec = load_round(round_file)
    defaults = load_model_defaults()
    model = build_model(model_name or defaults["default_model"], defaults=defaults)
    latest = spec.deadline_utc - timedelta(minutes=1)
    cut = ensure_utc(cutoff) if cutoff else min(datetime.now(UTC), latest)
    leagues = set(pipeline.leagues_config()["competitions"])
    with session_scope(_engine()) as session:
        history = load_history(session)
        registry = load_registry(session, TeamRegistry.from_config())
        resolved = resolve_round(spec, registry, rules, leagues)
        if not resolved.fixtures:
            raise click.ClickException("no fixtures could be resolved:\n  "
                                       + "\n  ".join(resolved.block_reasons))
        try:
            fc = forecast_round(history, resolved, cut, model, rules,
                                defaults.get("golden_goal"))
        except CutoffAfterKickoffError as exc:
            raise click.ClickException(str(exc)) from exc
        status = fc.status.value
        if save and spec.illustrative:
            click.secho("illustrative round: forecast shown but not saved", fg="yellow")
        elif save:
            run = persist_forecast(session, resolved, fc, publish=publish)
            status = run.status
        payload = forecast_to_json(fc, registry, status=status)
    click.echo(f"round {payload['round_id']} · run {payload['prediction_run_id']} · "
               f"{payload['status']} · model {payload['model_version']} · cutoff "
               f"{payload['cutoff_at']}\n")
    _print_forecast(payload)
    if json_path:
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(json_path).write_text(json.dumps(payload, indent=2))
        click.echo(f"\nwrote {json_path}")
    if payload["block_reasons"]:
        sys.exit(2)


# --------------------------------------------------------------------------- rounds
@main.group("round")
def round_group() -> None:
    """Create round files."""


@round_group.command("import")
@click.argument("text", required=False)
@click.option("--file", "text_file", type=click.Path(exists=True, dir_okay=False), default=None,
              help="Read the matchups from a file instead (one per line)")
@click.option("--round", "round_number", type=int, default=None, help="Super 6 round number")
@click.option("--date", "round_date", default=None,
              help="Main match day (YYYY-MM-DD, UK). Default: the coming Saturday")
@click.option("--fetch/--no-fetch", default=True,
              help="Refresh the provider's upcoming fixtures and odds first")
@click.option("--predict/--no-predict", "run_predict", default=True,
              help="Forecast the round straight away")
@click.option("--out", "out_path", type=click.Path(dir_okay=False), default=None)
@click.option("--history", "past_round", is_flag=True,
              help="A past round from the app's results: saved to rounds/history/ for "
                   "`super6 replay-rounds`, without fetching or forecasting")
@click.option("--golden-goal-minute", type=click.IntRange(0, 90), default=None,
              help="Past rounds: the official Golden Goal answer shown in the app (0 = no goals)")
@click.option("--source-note", default=None, hidden=True)
@click.pass_context
def round_import(ctx: click.Context, text: str | None, text_file: str | None,
                 round_number: int | None, round_date: str | None, fetch: bool,
                 run_predict: bool, out_path: str | None, past_round: bool,
                 golden_goal_minute: int | None, source_note: str | None) -> None:
    """Build a round file from the six matchups shown in the Super 6 app.

    TEXT is the six matchups, e.g. "Fulham v Newcastle, Man Utd v Aston Villa, ...".
    Competitions and kick-off times are filled in from the provider's fixture list; the
    deadline is the first kick-off. With --history, it records a past round (pass --date
    and, if known, --golden-goal-minute) for replay.
    """
    if past_round:
        if round_date is None:
            raise click.UsageError("--history needs --date (the round's main match day)")
        fetch, run_predict = False, False
    elif golden_goal_minute is not None:
        raise click.UsageError("--golden-goal-minute is only for past rounds (--history)")
    from datetime import date as date_cls

    from super6.ingestion.round_import import (
        RoundImportError,
        import_round,
        next_saturday,
        parse_fixture_text,
    )
    from super6.storage.repositories import load_history, load_registry

    raw = Path(text_file).read_text(encoding="utf-8") if text_file else text
    if raw is None:
        raw = sys.stdin.read()
    rules = load_rules()
    leagues = set(pipeline.leagues_config()["competitions"])
    day = (date_cls.fromisoformat(round_date) if round_date
           else next_saturday(datetime.now(UK).date()))
    settings = Settings.from_env()
    try:
        pairs = parse_fixture_text(raw)
        with session_scope(_engine()) as session:
            if fetch:
                pipeline.ingest_upcoming(session, settings.data_dir / "raw", log=click.echo)
            history = load_history(session)
            registry = load_registry(session, TeamRegistry.from_config())
            imported = import_round(pairs, registry, history, day, round_number=round_number,
                                    known_competitions=leagues)
    except RoundImportError as exc:
        raise click.ClickException("round not imported:\n  " + "\n  ".join(exc.problems)) from exc
    if len(imported.fixtures) != rules.fixtures_per_round:
        click.secho(f"note: {len(imported.fixtures)} fixtures (a full round has "
                    f"{rules.fixtures_per_round}); the prediction will be BLOCKED", fg="yellow")
    click.echo(_table(["fixture", "comp", "kick-off (UK)", "from"], [
        [f"{registry.name(f.home_team)} v {registry.name(f.away_team)}", f.competition,
         f.kickoff_utc.astimezone(UK).strftime("%a %d %b %H:%M"),
         {"provider_fixture_list": "provider fixture list", "text": "matchup text",
          "assumed": "ASSUMED 15:00"}[f.source]]
        for f in imported.fixtures]))
    first = imported.deadline_utc.astimezone(UK)
    click.echo(f"deadline (first kick-off): {first:%a %d %b %H:%M} UK")
    for w in imported.warnings:
        click.secho(f"warning: {w}", fg="yellow")
    folder = project_root() / "rounds" / ("history" if past_round else "")
    target = Path(out_path) if out_path else folder / f"{imported.round_id}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC"
    note = (f"{source_note} ({stamp})" if source_note
            else f"Imported from the Super 6 app matchups on {stamp}")
    # Pasted from the app = confirmed by the person pasting; fetched = not yet.
    confirmation = (None if source_note is not None else
                    (datetime.now(UTC), "Six matchups pasted from the Super 6 app "
                                        + ("results (past round)" if past_round else
                                           "(`super6 round import`)")))
    target.write_text(imported.to_yaml(rules.rules_version, note, confirmation,
                                       golden_goal_minute),
                      encoding="utf-8")
    if confirmation is None:
        click.secho("These fixtures were found automatically and may be wrong. Check them in "
                    f"the Super 6 app, then run `super6 round confirm {imported.round_id}`.\n"
                    "Until then the forecast can't be locked.", fg="yellow")
    click.echo(f"wrote {target}")
    if run_predict:
        click.echo("")
        ctx.invoke(predict, round_file=str(target), model_name=None, cutoff=None, publish=False,
                   json_path=str(settings.reports_dir / f"{imported.round_id}.json"), save=True)


@round_group.command("fetch")
@click.option("--round", "round_number", type=int, default=None, help="Super 6 round number")
@click.option("--date", "round_date", default=None,
              help="Main match day (YYYY-MM-DD, UK). Default: the coming Saturday")
@click.option("--predict/--no-predict", "run_predict", default=True)
@click.pass_context
def round_fetch(ctx: click.Context, round_number: int | None, round_date: str | None,
                run_predict: bool) -> None:
    """Find this round's six fixtures in public preview articles, then import them.

    Reads Sky Sports' public news pages (robots.txt respected, honest User-Agent) for
    headings marked "PLAY SUPER 6!". If exactly six are found, they go through
    `round import`; otherwise nothing is written and you can paste them instead.
    """
    from datetime import date as date_cls

    from super6.ingestion.round_import import next_saturday
    from super6.ingestion.round_sources import PoliteFetcher, find_round_lines

    day = (date_cls.fromisoformat(round_date) if round_date
           else next_saturday(datetime.now(UK).date()))
    fetcher = PoliteFetcher(raw_dir=Settings.from_env().data_dir / "raw" / "round_sources")
    report = find_round_lines(day, fetcher)
    for art in report.articles:
        click.echo(f"source: {art['url']} (published {art['published_at'][:16]}, "
                   f"{art['super6_matches']} Super 6 matches)")
    for skipped in report.skipped:
        click.echo(f"skipped: {skipped}")
    if len(report.lines) != 6:
        found = "\n  ".join(report.lines) or "(none)"
        raise click.ClickException(
            f"found {len(report.lines)} Super 6 matches, expected 6:\n  {found}\n"
            "Paste the six from the app instead: super6 round import \"Home v Away, ...\"")
    sources = ", ".join(a["url"] for a in report.articles if a["super6_matches"])
    ctx.invoke(round_import, text="\n".join(report.lines), text_file=None,
               round_number=round_number, round_date=day.isoformat(), fetch=True,
               run_predict=run_predict, out_path=None, source_note=f"Found in: {sources}")


@round_group.command("confirm")
@click.argument("round_id")
@click.option("--source", "confirmed_source", default="Checked against the Super 6 app",
              show_default=True, help="How the six fixtures were checked (recorded in the file)")
def round_confirm(round_id: str, confirmed_source: str) -> None:
    """Record that a round's fixtures were checked against the Super 6 app."""
    import yaml as yaml_mod

    from super6.prediction.round_spec import RoundSpec

    path = project_root() / "rounds" / f"{round_id}.yaml"
    if not path.exists():
        raise click.ClickException(f"no round file {path}")
    data = yaml_mod.safe_load(path.read_text(encoding="utf-8"))
    load_round(path)  # validate before changing anything
    data.update(confirmed=True, confirmed_at=iso(datetime.now(UTC)),
                confirmed_source=confirmed_source)
    RoundSpec.model_validate(data)
    path.write_text("# Fixtures confirmed against the Super 6 app.\n"
                    + yaml_mod.safe_dump(data, sort_keys=False), encoding="utf-8")
    click.echo(f"{round_id} confirmed ({confirmed_source}); the next forecast can be locked")


# --------------------------------------------------------------------------- cycle
@main.command()
@click.option("--now", "now_text", default=None, hidden=True,
              help="Pretend the time is this (ISO UTC); for testing")
@click.option("--fetch-round/--no-fetch-round", default=True,
              help="Look for a missing round in public previews")
@click.option("--lock-window-minutes", type=float, default=60.0, show_default=True)
@click.option("--void-after-days", type=float, default=3.0, show_default=True)
def cycle(now_text: str | None, fetch_round: bool, lock_window_minutes: float,
          void_after_days: float) -> None:
    """Run the round workflow once (schedule it hourly). Idempotent.

    Refreshes data, finds or fetches the round, re-forecasts when inputs change, locks
    inside the lock window before the deadline, and settles after the matches. Alerts go
    to reports/alerts.jsonl (and SUPER6_ALERT_WEBHOOK_URL if set). Exits 1 on errors.
    """
    from super6.ingestion.round_sources import PoliteFetcher, find_round_lines
    from super6.jobs.alerts import AlertSink
    from super6.jobs.cycle import CycleConfig, CycleDeps, provider_refresh, run_cycle
    from super6.storage.repositories import load_history

    settings = Settings.from_env()
    rules = load_rules()
    defaults = load_model_defaults()
    leagues_cfg = pipeline.leagues_config()
    cfg = CycleConfig(lock_window_minutes=lock_window_minutes, void_after_days=void_after_days,
                      fetch_rounds=fetch_round)
    fetcher = PoliteFetcher(raw_dir=settings.data_dir / "raw" / "round_sources")
    deps = CycleDeps(
        refresh=provider_refresh(settings.data_dir, cfg,
                                 list(leagues_cfg["default_ingest"]["competitions"])),
        load_history=load_history,
        fetch_round_lines=lambda d: find_round_lines(d, fetcher),
        model_factory=lambda: build_model(defaults["default_model"], defaults=defaults),
        rounds_dir=project_root() / "rounds",
        known_competitions=set(leagues_cfg["competitions"]),
        golden_goal_config=defaults.get("golden_goal", {}),
    )
    alerts = AlertSink(settings.reports_dir / "alerts.jsonl")
    now = ensure_utc(now_text) if now_text else None
    report = run_cycle(_engine(), rules, deps, alerts, now, cfg)
    for msg in report.refresh:
        click.echo(f"refresh: {msg}")
    for r in report.rounds:
        click.echo(f"{r.round_id} (deadline {r.deadline_utc}): {', '.join(r.actions)}"
                   + (f" · {r.run_id} {r.status}" if r.run_id else ""))
        for line in r.picks:
            click.echo(f"    {line}")
    for a in alerts.emitted:
        click.echo(f"{a.severity.upper()} {a.code}: {a.message}")
    out = settings.reports_dir / "cycle" / f"cycle-{report.at.replace(':', '')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"at": report.at, "refresh": report.refresh,
                               "rounds": [r.__dict__ for r in report.rounds],
                               "alerts": [a.__dict__ for a in alerts.emitted]}, indent=2))
    if alerts.has_errors:
        sys.exit(1)


@main.command()
@click.argument("text")
@click.option("--date", "round_date", default=None,
              help="Main match day (YYYY-MM-DD, UK). Default: the coming Saturday")
@click.option("--model", "model_name", default=None, type=click.Choice(sorted(MODEL_CLASSES)))
@click.option("--fetch/--no-fetch", default=True, help="Refresh upcoming fixtures and odds")
def forecast(text: str, round_date: str | None, model_name: str | None, fetch: bool) -> None:
    """Forecast any list of fixtures, e.g. a whole Premier League weekend. Nothing is saved.

    TEXT uses the same format as `round import`, e.g. "Leeds vs Tottenham, Saturday 12.30pm;
    Brighton vs Brentford, Sunday 2pm". This is not a Super 6 round: there is no six-fixture
    check, no Golden Goal, and no audit record.
    """
    from datetime import date as date_cls

    from super6.domain import FixtureSpec
    from super6.ingestion.adapters.football_data_couk import fixture_key
    from super6.ingestion.round_import import (
        RoundImportError,
        import_round,
        next_saturday,
        parse_fixture_text,
    )
    from super6.prediction.optimiser import Objective, choose_score
    from super6.prediction.service import forecast_basis
    from super6.storage.repositories import load_history, load_registry

    rules = load_rules()
    defaults = load_model_defaults()
    leagues = set(pipeline.leagues_config()["competitions"])
    day = (date_cls.fromisoformat(round_date) if round_date
           else next_saturday(datetime.now(UK).date()))
    try:
        pairs = parse_fixture_text(text)
        with session_scope(_engine()) as session:
            if fetch:
                pipeline.ingest_upcoming(session, Settings.from_env().data_dir / "raw",
                                         log=lambda _: None)
            history = load_history(session)
            registry = load_registry(session, TeamRegistry.from_config())
            imported = import_round(pairs, registry, history, day, known_competitions=leagues)
    except RoundImportError as exc:
        raise click.ClickException("\n  ".join(exc.problems)) from exc
    now = datetime.now(UTC)
    fixtures = sorted(
        (FixtureSpec(fixture_key(f.competition, f.kickoff_utc.astimezone(UK).date(),
                                 f.home_team, f.away_team),
                     f.competition, f.home_team, f.away_team, f.kickoff_utc)
         for f in imported.fixtures if f.kickoff_utc > now),
        key=lambda f: (f.kickoff_utc, f.fixture_id))
    skipped = len(imported.fixtures) - len(fixtures)
    model = build_model(model_name or defaults["default_model"], defaults=defaults)
    view = history.view(now)
    preds = model.fit(view).predict_many(fixtures, view)
    rows = []
    for fx, pred in zip(fixtures, preds, strict=True):
        pts, _ = choose_score(pred.distribution, Objective.POINTS, rules, 0)
        jp, _ = choose_score(pred.distribution, Objective.JACKPOT, rules, 0)
        o = pred.distribution.outcome_probabilities()
        rows.append([fx.kickoff_utc.astimezone(UK).strftime("%a %H:%M"),
                     f"{registry.name(fx.home_team)} v {registry.name(fx.away_team)}",
                     pts.score, jp.score, _pct(jp.exact_probability),
                     f"{_pct(o.home)}/{_pct(o.draw)}/{_pct(o.away)}",
                     f"{pred.lam_home:.2f}/{pred.lam_away:.2f}", forecast_basis(pred)])
    click.echo(_table(["kick-off (UK)", "fixture", "points", "jackpot", "P(jackpot pick)",
                       "H/D/A", "xG h/a", "basis"], rows))
    if skipped:
        click.echo(f"\n{skipped} fixture(s) already kicked off and were skipped")
    for w in imported.warnings:
        click.secho(f"warning: {w}", fg="yellow")


# --------------------------------------------------------------------------- runs
@main.group()
def runs() -> None:
    """Inspect, lock and settle prediction runs."""


@runs.command("list")
@click.option("--round", "round_id", default=None)
def runs_list(round_id: str | None) -> None:
    from super6.storage.models import ModelRegistryEntry, PredictionRun

    with session_scope(_engine()) as session:
        stmt = select(PredictionRun, ModelRegistryEntry.model_name).join(
            ModelRegistryEntry, ModelRegistryEntry.id == PredictionRun.model_registry_id
        ).order_by(PredictionRun.created_at)
        if round_id:
            stmt = stmt.where(PredictionRun.round_id == round_id)
        rows = [[r.id, r.round_id, m, r.status, iso(r.cutoff_at), iso(r.created_at)]
                for r, m in session.execute(stmt).all()]
    click.echo(_table(["run", "round", "model", "status", "cutoff", "created"], rows))


@runs.command("show")
@click.argument("run_id")
def runs_show(run_id: str) -> None:
    from super6.storage.models import (
        Evaluation,
        FixtureForecast,
        GoldenGoalPrediction,
        PredictionRun,
        Recommendation,
        Team,
    )
    from super6.storage.models import Fixture as FixtureRow

    with session_scope(_engine()) as session:
        run = session.get(PredictionRun, run_id)
        if run is None:
            raise click.ClickException(f"no run {run_id}")
        names = dict(session.execute(select(Team.id, Team.canonical_name)).all())
        click.echo(f"{run.id} · round {run.round_id} · {run.status} · cutoff "
                   f"{iso(run.cutoff_at)} · code {run.code_version}")
        recs = {(r.mode, r.fixture_id): r for r in session.scalars(
            select(Recommendation).where(Recommendation.run_id == run_id))}
        rows = []
        for ff in session.scalars(select(FixtureForecast).where(
                FixtureForecast.run_id == run_id).order_by(FixtureForecast.display_order)):
            fx = session.get(FixtureRow, ff.fixture_id)
            assert fx is not None
            p, j = recs[("points", ff.fixture_id)], recs[("jackpot", ff.fixture_id)]
            rows.append([f"{names[fx.home_team_id]} v {names[fx.away_team_id]}",
                         f"{p.home_goals}-{p.away_goals}", f"{j.home_goals}-{j.away_goals}",
                         _pct(j.exact_probability),
                         f"{_pct(ff.p_home)}/{_pct(ff.p_draw)}/{_pct(ff.p_away)}"])
        click.echo(_table(["fixture", "points", "jackpot", "P(jackpot pick)", "H/D/A"], rows))
        gg = session.get(GoldenGoalPrediction, run_id)
        if gg:
            click.echo(f"Golden Goal: {gg.minute} ({gg.interval_low}–{gg.interval_high}) "
                       f"[{gg.method}]")
        evals = session.scalars(select(Evaluation).where(Evaluation.run_id == run_id)).all()
        if evals:
            click.echo("evaluation: " + ", ".join(f"{e.mode}.{e.metric}={e.value:g}"
                                                  for e in evals))
        if run.block_reasons:
            click.secho("blocked: " + "; ".join(run.block_reasons), fg="red")


@runs.command("lock")
@click.argument("run_id")
def runs_lock(run_id: str) -> None:
    """Freeze a run as the round's pre-deadline record (irreversible)."""
    from super6.prediction.service import LockError, lock_run

    with session_scope(_engine()) as session:
        try:
            run = lock_run(session, run_id)
        except LockError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"{run.id} LOCKED at {iso(run.locked_at)}" if run.locked_at else run.id)


@main.command()
@click.argument("round_id")
@click.option("--allow-missing", is_flag=True, help="Score settled fixtures even if some are void")
@click.option("--golden-goal-minute", type=int, default=None,
              help="The official Golden Goal minute from the app (0 = no goals). Without "
                   "it, the minute is worked out from stored goal minutes when possible")
def settle(round_id: str, allow_missing: bool, golden_goal_minute: int | None) -> None:
    """Score a round's runs against ingested results (run `data ingest` first)."""
    from super6.prediction.service import settle_round

    rules = load_rules()
    with session_scope(_engine()) as session:
        results = settle_round(session, round_id, rules, allow_missing, golden_goal_minute)
    for s in results:
        if s.missing_fixtures and not s.metrics:
            click.echo(f"{s.run_id}: waiting for results ({', '.join(s.missing_fixtures)})")
            continue
        m = s.metrics
        click.echo(f"{s.run_id} [{s.status}]: points mode {m['points']['points']:g} pts "
                   f"({m['points']['exact_hits']:g} exact), jackpot mode "
                   f"{m['jackpot']['points']:g} pts ({m['jackpot']['exact_hits']:g} exact)")
        g = m.get("golden_goal")
        if g:
            source = "goal minutes" if g["from_goal_events"] else "official"
            click.echo(f"  Golden Goal: predicted {g['predicted_minute']:g}, actual "
                       f"{g['actual_minute']:g} ({source}), error {g['abs_error']:g}")
            matches = {k.removeprefix("official_matches_"): v for k, v in g.items()
                       if k.startswith("official_matches_")}
            if matches:
                agree = [k for k, v in matches.items() if v] or ["neither reading"]
                click.echo(f"  The official answer matches: {', '.join(agree)} "
                           f"(rules assume {rules.golden_goal_first_goal})")
        else:
            click.echo("  Golden Goal: not settled (pass --golden-goal-minute, or run "
                       "`data goals` once goal minutes are published)")


# --------------------------------------------------------------------------- analysis
@main.command()
@click.option("--start", required=True, help="Test window start date (UTC), e.g. 2023-07-01")
@click.option("--end", required=True, help="Test window end date (exclusive)")
@click.option("--models", default=",".join(sorted(MODEL_CLASSES)), show_default=True)
@click.option("--reference", default="ensemble", show_default=True)
@click.option("--competitions", default="E0,E1,E2,E3,SC0", show_default=True,
              help="Fixtures to forecast (training always uses all ingested competitions)")
@click.option("--refit-every-days", type=float, default=None)
@click.option("--odds-basis", default=None,
              help="Use only odds whose timestamps are known this way, e.g. 'observed,kickoff' "
                   "to exclude prices with schedule-inferred timestamps (default: all)")
@click.option("--label", default=None)
@click.option("--out", "out_dir", type=click.Path(file_okay=False), default=None)
def backtest(start: str, end: str, models: str, reference: str, competitions: str,
             refit_every_days: float | None, odds_basis: str | None, label: str | None,
             out_dir: str | None) -> None:
    """Chronological walk-forward backtest against baselines."""
    from super6.evaluation.replay import synthetic_rounds, walk_forward
    from super6.evaluation.reports import build_report, write_report
    from super6.storage.repositories import load_history

    rules = load_rules()
    defaults = load_model_defaults()
    bt = defaults.get("backtest", {})
    names = [m.strip() for m in models.split(",") if m.strip()]
    if reference not in names:
        raise click.ClickException(f"reference {reference!r} must be one of --models")
    built = {n: build_model(n, defaults=defaults) for n in names}
    comps = [c for c in competitions.split(",") if c]
    refit = refit_every_days if refit_every_days is not None else bt.get("refit_every_days", 1)
    with session_scope(_engine()) as session:
        history = load_history(session)
    bases = {b.strip() for b in odds_basis.split(",") if b.strip()} if odds_basis else None
    if bases is not None:
        history = history.restrict_odds(bases)
    start_ts, end_ts = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    rows = walk_forward(history, built, rules, start_ts, end_ts, comps, refit,
                        progress=lambda m: click.echo(m, err=True))
    if rows.empty:
        raise click.ClickException("no finished fixtures in that window")
    sr = bt.get("synthetic_rounds", {})
    rounds = synthetic_rounds(rows, sr.get("competitions", ["E0", "E1"]), sr.get("weekday", 5),
                              sr.get("local_kickoff", "15:00"), sr.get("fixtures_per_round", 6),
                              sr.get("seed", 6))
    label = label or f"{start}_{end}"
    meta = {"label": label, "start": start, "end": end, "competitions": comps,
            "models": names, "reference": reference, "refit_every_days": refit,
            "odds_basis": sorted(bases) if bases is not None else "all",
            "rules_version": rules.rules_version, "rules_verified": rules.verified,
            "code_version": code_version(), "model_config": {n: built[n].config for n in names},
            "synthetic_competitions": sr.get("competitions", ["E0", "E1"])}
    boot = bt.get("bootstrap", {})
    report = build_report(rows, rounds, reference=reference, meta=meta,
                          resamples=boot.get("resamples", 2000), seed=boot.get("seed", 7))
    target = Path(out_dir) if out_dir else Settings.from_env().reports_dir / f"backtest-{label}"
    paths = write_report(report, rows, rounds, target)
    from super6.evaluation.tracking import experiment_record, log_experiment

    log = log_experiment(experiment_record(meta, report.summary, paths),
                         Settings.from_env().reports_dir)
    click.echo(report.markdown)
    click.echo(f"\nwrote {paths['markdown']} (logged in {log})")


@main.command()
@click.option("--last", default=20, show_default=True, help="Show the most recent N runs")
def experiments(last: int) -> None:
    """List logged backtests and experiments (best model by exact-score log loss)."""
    from super6.evaluation.tracking import read_log

    records = read_log(Settings.from_env().reports_dir)[-last:]
    if not records:
        click.echo("no experiments logged yet (run `super6 backtest` or scripts/run_experiment.py)")
        return
    rows = []
    for r in records:
        overall = r.get("overall") or []  # experiments other than score models have none
        best = min(overall, key=lambda m: m["exact_log_loss"]) if overall else {}
        logged = str(r.get("logged_at") or r.get("id", "").partition("@")[2])
        rows.append([logged[:16].replace("T", " "), r["label"],
                     f"{r['window'][0]} → {r['window'][1]}", len(overall) or "-",
                     best.get("model", "-"), f"{best.get('exact_log_loss', float('nan')):.4f}",
                     r.get("code_version", "")])
    click.echo(_table(["logged (UTC)", "label", "window", "models", "best", "log loss",
                       "code"], rows))


@main.command("replay-rounds")
@click.argument("round_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--model", "model_name", default=None, type=click.Choice(sorted(MODEL_CLASSES)))
@click.option("--allow-illustrative", is_flag=True,
              help="Also replay files marked illustrative (for demonstrations only)")
@click.option("--out", "out_path", type=click.Path(dir_okay=False), default=None)
def replay_rounds_cmd(round_dir: str, model_name: str | None, allow_illustrative: bool,
                      out_path: str | None) -> None:
    """Replay verified historical Super 6 rounds at their real deadlines and score them."""
    from super6.evaluation.rounds import replay_rounds, summarise_rounds
    from super6.storage.repositories import load_history, load_registry

    rules = load_rules()
    defaults = load_model_defaults()
    name = model_name or defaults["default_model"]
    files = sorted(Path(round_dir).glob("*.yaml"))
    if not files:
        raise click.ClickException(f"no round files (*.yaml) in {round_dir}")
    specs = [load_round(f) for f in files]
    leagues = set(pipeline.leagues_config()["competitions"])
    with session_scope(_engine()) as session:
        history = load_history(session)
        registry = load_registry(session, TeamRegistry.from_config())
    frame = replay_rounds(history, specs, registry, rules,
                          lambda: build_model(name, defaults=defaults), leagues,
                          defaults.get("golden_goal"), allow_illustrative)
    cols = [c for c in ("round_id", "kind", "status", "points_points", "points_exact",
                        "jackpot_points", "jackpot_exact", "golden_goal_minute", "detail")
            if c in frame.columns]
    click.echo(_table(cols, frame[cols].fillna("").to_numpy().tolist()))
    click.echo("\n" + json.dumps(summarise_rounds(frame), indent=2, default=str))
    if out_path:
        frame.to_csv(out_path, index=False)
        click.echo(f"wrote {out_path}")


@main.command()
@click.option("--cutoff", default=None, help="ISO UTC timestamp (default: now)")
@click.option("--competition", default="E0", show_default=True)
@click.option("--model", "model_name", default="dixon_coles",
              type=click.Choice(["dixon_coles", "poisson"]))
def ratings(cutoff: str | None, competition: str, model_name: str) -> None:
    """Team attack/defence ratings as of a cutoff (model explanation)."""
    from super6.models.poisson import FittedTeamStrength
    from super6.storage.repositories import load_history

    with session_scope(_engine()) as session:
        history = load_history(session)
        names = load_registry_names(session)
    cut = ensure_utc(cutoff) if cutoff else datetime.now(UTC)
    view = history.view(cut)
    fitted = build_model(model_name).fit(view)
    assert isinstance(fitted, FittedTeamStrength)
    res = view.results
    recent = res.loc[res["competition"] == competition]
    season_teams = set(recent.loc[recent["kickoff_utc"] >= recent["kickoff_utc"].max()
                                  - pd.Timedelta(days=120), "home_team"])
    table = fitted.ratings().loc[lambda d: d.index.isin(season_teams)]
    click.echo(f"{model_name} ratings for {competition} as of {iso(cut)} "
               f"(home advantage {fitted.home_advantage.get(competition, float('nan')):.3f}, "
               f"rho {fitted.rho:.3f})")
    click.echo(_table(["team", "attack", "defence", "net", "eff. matches"], [
        [names.get(str(t), str(t)), f"{r.attack:+.3f}", f"{r.defence:+.3f}", f"{r.net:+.3f}",
         f"{r.effective_matches:.1f}"] for t, r in table.iterrows()]))


def load_registry_names(session: Any) -> dict[str, str]:
    from super6.storage.models import Team

    return dict(session.execute(select(Team.id, Team.canonical_name)).all())


if __name__ == "__main__":
    main()
