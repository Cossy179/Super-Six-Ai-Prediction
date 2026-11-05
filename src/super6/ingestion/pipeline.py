"""Download → raw snapshot → parse/validate → canonical tables."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from super6.config import load_yaml
from super6.ingestion.adapters import football_data_couk as fdc
from super6.storage.repositories import (
    IngestStats,
    ingest_parsed,
    load_registry,
    sync_competitions,
)


def leagues_config() -> dict[str, Any]:
    return load_yaml("leagues.yaml")


def licence_id(source: str = fdc.SOURCE) -> str:
    return str(load_yaml("data_sources.yaml")["sources"][source]["licence_id"])


def raw_season_files(raw_dir: Path) -> list[tuple[Path, str, str]]:
    """(path, competition, season label) for every downloaded season CSV."""
    out = []
    for path in sorted((raw_dir / fdc.SOURCE).glob("*/*.csv")):
        if path.parent.name.isdigit() and len(path.parent.name) == 4:
            out.append((path, path.stem, fdc.season_label_from_code(path.parent.name)))
    return out


def ingest_raw_dir(
    session: Session,
    raw_dir: Path,
    competitions: Iterable[str] | None = None,
    log: Callable[[str], Any] = print,
) -> list[IngestStats]:
    leagues = leagues_config()["competitions"]
    sync_competitions(session, leagues)
    registry = load_registry(session)
    wanted = set(competitions) if competitions is not None else set(leagues)
    results: list[IngestStats] = []
    for path, comp, season in raw_season_files(raw_dir):
        if comp not in wanted:
            continue
        raw = fdc.load_local(path)
        parsed = fdc.parse_csv(raw, registry, competition=comp, season_label=season)
        stats = ingest_parsed(session, raw, parsed, licence_id())
        results.append(stats)
        if stats.skipped:
            log(f"unchanged {comp} {season}")
        else:
            log(f"ingested {comp} {season}: +{stats.fixtures_new} fixtures, "
                f"+{stats.results_new} results, +{stats.odds_new} odds rows, "
                f"{stats.issues} issues, {stats.result_conflicts} conflicts")
    return results


def ingest_upcoming(session: Session, raw_dir: Path, log: Callable[[str], Any] = print
                    ) -> IngestStats:
    """Fetch fixtures.csv (upcoming fixtures with current odds) and ingest configured leagues."""
    leagues = leagues_config()["competitions"]
    sync_competitions(session, leagues)
    registry = load_registry(session)
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    raw = fdc.fetch(f"{fdc.BASE_URL}/fixtures.csv",
                    raw_dir / fdc.SOURCE / "fixtures" / f"fixtures-{stamp}.csv")
    parsed = fdc.parse_csv(raw, registry, known_competitions=leagues)
    stats = ingest_parsed(session, raw, parsed, licence_id())
    log(f"upcoming fixtures: +{stats.fixtures_new} new, {stats.fixtures_updated} updated, "
        f"+{stats.odds_new} odds rows")
    return stats
