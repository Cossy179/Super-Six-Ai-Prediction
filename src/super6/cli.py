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
        click.echo(_table(["kickoff (UK)", "comp", "home", "away", "kickoff_utc"], [
            [f.kickoff_utc.astimezone(UK).strftime("%a %d %b %H:%M"), f.competition_id,
             names.get(f.home_team_id, f.home_team_id), names.get(f.away_team_id, f.away_team_id),
             iso(f.kickoff_utc)] for f in rows]))


@data.command("status")
def data_status() -> None:
    """Coverage, freshness and data-quality summary."""
    from super6.storage.models import DataQualityIssue, Fixture, FixtureResult, SourceSnapshot

    with session_scope(_engine()) as session:
        rows = session.execute(
            select(Fixture.competition_id, Fixture.season, func.count(FixtureResult.id),
                   func.max(FixtureResult.result_available_at))
            .join(FixtureResult, FixtureResult.fixture_id == Fixture.id)
            .group_by(Fixture.competition_id, Fixture.season)
            .order_by(Fixture.competition_id, Fixture.season)).all()
        click.echo(_table(["comp", "season", "results", "latest result (UTC)"],
                          [[c, s, n, iso(t)] for c, s, n, t in rows]))
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
        ])
    click.echo(_table(["fixture", "points", "jackpot", "P(jackpot pick)", "E[pts]", "H/D/A",
                       "xG h/a"], rows))
    m = payload["modes"]
    click.echo(f"\nexpected points: points mode {m['points']['expected_points']:.2f}, "
               f"jackpot mode {m['jackpot']['expected_points']:.2f}")
    click.echo(f"P(all six jackpot picks exact) ≈ {m['jackpot']['joint_exact_probability']:.2e} "
               "(assumes independent matches)")
    gg = payload.get("golden_goal")
    if gg:
        lo, hi = gg["interval_80"]
        click.echo(f"Golden Goal: minute {gg['minute']} (80% interval {lo}–{hi}, expected "
                   f"error ±{gg['expected_absolute_error_minutes']} min) [{gg['method']} — "
                   "baseline, no goal-timing data]")
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
        if save:
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
def settle(round_id: str, allow_missing: bool) -> None:
    """Score a round's runs against ingested results (run `data ingest` first)."""
    from super6.prediction.service import settle_round

    rules = load_rules()
    with session_scope(_engine()) as session:
        results = settle_round(session, round_id, rules, allow_missing)
    for s in results:
        if s.missing_fixtures and not s.metrics:
            click.echo(f"{s.run_id}: waiting for results ({', '.join(s.missing_fixtures)})")
            continue
        m = s.metrics
        click.echo(f"{s.run_id} [{s.status}]: points mode {m['points']['points']:g} pts "
                   f"({m['points']['exact_hits']:g} exact), jackpot mode "
                   f"{m['jackpot']['points']:g} pts ({m['jackpot']['exact_hits']:g} exact)")


# --------------------------------------------------------------------------- analysis
@main.command()
@click.option("--start", required=True, help="Test window start date (UTC), e.g. 2023-07-01")
@click.option("--end", required=True, help="Test window end date (exclusive)")
@click.option("--models", default=",".join(sorted(MODEL_CLASSES)), show_default=True)
@click.option("--reference", default="dixon_coles", show_default=True)
@click.option("--competitions", default="E0,E1,E2,E3,SC0", show_default=True,
              help="Fixtures to forecast (training always uses all ingested competitions)")
@click.option("--refit-every-days", type=float, default=None)
@click.option("--label", default=None)
@click.option("--out", "out_dir", type=click.Path(file_okay=False), default=None)
def backtest(start: str, end: str, models: str, reference: str, competitions: str,
             refit_every_days: float | None, label: str | None, out_dir: str | None) -> None:
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
            "rules_version": rules.rules_version, "rules_verified": rules.verified,
            "code_version": code_version(), "model_config": {n: built[n].config for n in names},
            "synthetic_competitions": sr.get("competitions", ["E0", "E1"])}
    boot = bt.get("bootstrap", {})
    report = build_report(rows, rounds, reference=reference, meta=meta,
                          resamples=boot.get("resamples", 2000), seed=boot.get("seed", 7))
    target = Path(out_dir) if out_dir else Settings.from_env().reports_dir / f"backtest-{label}"
    paths = write_report(report, rows, rounds, target)
    click.echo(report.markdown)
    click.echo(f"\nwrote {paths['markdown']}")


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
