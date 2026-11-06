"""Football-Data.co.uk CSV adapter.

Downloads season files (``/mmz4281/{2324}/{E0}.csv``) and the upcoming-fixtures file
(``/fixtures.csv``), keeps the raw bytes with a checksum and retrieval time, and parses
rows into canonical match, odds and stats records. Column sets differ between seasons, so
every optional field has explicit fallbacks and missing values stay missing.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from super6.domain import FixtureStatus
from super6.ingestion.canonicalize import TeamRegistry
from super6.ingestion.validate import QualityReport, check_score, season_bounds
from super6.timeutil import UK

SOURCE = "football_data_couk"
BASE_URL = "https://www.football-data.co.uk"
RESULT_LAG = timedelta(minutes=135)
ODDS_COLLECTION_HOUR = 18
DEFAULT_LOCAL_KICKOFF = (15, 0)

# Preferred column first. Average-of-bookmakers odds are the least bookmaker-specific.
PREMATCH_1X2 = [("AvgH", "AvgD", "AvgA", "market_average"),
                ("BbAvH", "BbAvD", "BbAvA", "market_average"),
                ("B365H", "B365D", "B365A", "bet365"),
                ("PSH", "PSD", "PSA", "pinnacle")]
PREMATCH_OU = [("Avg>2.5", "Avg<2.5"), ("BbAv>2.5", "BbAv<2.5"), ("B365>2.5", "B365<2.5"),
               ("P>2.5", "P<2.5")]
CLOSING_1X2 = [("AvgCH", "AvgCD", "AvgCA", "market_average"),
               ("B365CH", "B365CD", "B365CA", "bet365"),
               ("PSCH", "PSCD", "PSCA", "pinnacle")]
CLOSING_OU = [("AvgC>2.5", "AvgC<2.5"), ("B365C>2.5", "B365C<2.5"), ("PC>2.5", "PC<2.5")]
STATS = {"home_shots": "HS", "away_shots": "AS", "home_shots_on_target": "HST",
         "away_shots_on_target": "AST", "home_xg": "HxG", "away_xg": "AxG"}


def season_code(label: str) -> str:
    """'2023-24' → '2324'."""
    start = int(label[:4])
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def season_label_from_code(code: str) -> str:
    """'2324' → '2023-24' (football-data archives start in 1993)."""
    start = int(code[:2])
    year = 1900 + start if start >= 93 else 2000 + start
    return f"{year}-{(year + 1) % 100:02d}"


def seasons_between(first: str, last: str) -> list[str]:
    return [f"{y}-{(y + 1) % 100:02d}" for y in range(int(first[:4]), int(last[:4]) + 1)]


def season_for_date(d: date) -> str:
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


@dataclass(frozen=True)
class RawFile:
    source: str
    uri: str
    local_path: Path
    retrieved_at: datetime
    checksum: str
    content: bytes


@dataclass
class ParsedFile:
    matches: list[dict[str, Any]] = field(default_factory=list)
    odds: list[dict[str, Any]] = field(default_factory=list)
    stats: list[dict[str, Any]] = field(default_factory=list)
    teams: dict[str, str] = field(default_factory=dict)  # team_id -> source name
    display_names: dict[str, str] = field(default_factory=dict)  # team_id -> display name
    report: QualityReport | None = None


# ---------------------------------------------------------------------- download
def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def fetch(
    uri: str,
    dest: Path,
    client: httpx.Client | None = None,
    *,
    retries: int = 4,
) -> RawFile:
    """Download ``uri`` to ``dest`` (with a JSON sidecar recording provenance)."""
    own = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=30.0,
                                    headers={"User-Agent": "super6-ai/0.1 (research)"})
    try:
        delay = 2.0
        for attempt in range(retries + 1):
            try:
                resp = client.get(uri)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                resp.raise_for_status()
                break
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                    exc.response.status_code == 429 or exc.response.status_code >= 500
                )
                if attempt == retries or not retryable:
                    raise
                time.sleep(delay)
                delay *= 2
    finally:
        if own:
            client.close()
    content = resp.content
    retrieved_at = datetime.now(UTC)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    raw = RawFile(SOURCE, uri, dest, retrieved_at, _sha256(content), content)
    dest.with_suffix(dest.suffix + ".meta.json").write_text(
        json.dumps({"source": SOURCE, "uri": uri, "retrieved_at": retrieved_at.isoformat(),
                    "checksum": raw.checksum, "bytes": len(content)}, indent=2)
    )
    return raw


def load_local(path: Path) -> RawFile:
    """Re-open a previously downloaded file using its provenance sidecar."""
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    content = path.read_bytes()
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        uri, retrieved = meta["uri"], datetime.fromisoformat(meta["retrieved_at"])
    else:
        uri = path.resolve().as_uri()
        retrieved = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return RawFile(SOURCE, uri, path, retrieved, _sha256(content), content)


def season_uri(competition: str, season_label: str, base_url: str = BASE_URL) -> str:
    return f"{base_url}/mmz4281/{season_code(season_label)}/{competition}.csv"


def download_seasons(
    competitions: Iterable[str],
    seasons: Iterable[str],
    raw_dir: Path,
    *,
    base_url: str = BASE_URL,
    pause_seconds: float = 1.5,
    refresh: bool = False,
    log: Any = print,
) -> list[RawFile]:
    files: list[RawFile] = []
    with httpx.Client(follow_redirects=True, timeout=30.0,
                      headers={"User-Agent": "super6-ai/0.1 (research)"}) as client:
        for season in seasons:
            for comp in competitions:
                dest = raw_dir / SOURCE / season_code(season) / f"{comp}.csv"
                if dest.exists() and not refresh:
                    files.append(load_local(dest))
                    continue
                uri = season_uri(comp, season, base_url)
                try:
                    files.append(fetch(uri, dest, client))
                    log(f"downloaded {uri}")
                except httpx.HTTPStatusError as exc:
                    log(f"skipped {uri}: HTTP {exc.response.status_code}")
                time.sleep(pause_seconds)
    return files


# ---------------------------------------------------------------------- parsing
def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def _parse_date(text: str) -> date | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_int(text: str | None) -> int | None:
    if text is None or text.strip() == "":
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return int(value) if value.is_integer() else None


def _parse_float(text: str | None) -> float | None:
    if text is None or text.strip() == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _first_odds(row: dict[str, str], options: Sequence[tuple[str, ...]]
                ) -> tuple[Any, ...] | None:
    for opt in options:
        cols, label = (opt[:3], opt[3]) if len(opt) == 4 else (opt, None)
        values = [_parse_float(row.get(c)) for c in cols]
        if all(v is not None and v > 1.0 for v in values):
            return (*values, label) if label else tuple(values)
    return None


def kickoff_utc(match_date: date, time_text: str | None) -> tuple[datetime, bool]:
    """UK-local date/time → UTC. Missing time → 15:00 local, flagged as unknown."""
    known = False
    hour, minute = DEFAULT_LOCAL_KICKOFF
    if time_text and time_text.strip():
        try:
            hh, mm = time_text.strip().split(":")[:2]
            hour, minute, known = int(hh), int(mm), True
        except ValueError:
            known = False
    local = datetime(match_date.year, match_date.month, match_date.day, hour, minute, tzinfo=UK)
    return local.astimezone(UTC), known


def result_available_at(kickoff: datetime, time_known: bool) -> datetime:
    """When the full-time result became public (conservative)."""
    if time_known:
        return kickoff + RESULT_LAG
    local = kickoff.astimezone(UK)
    end_of_day = datetime(local.year, local.month, local.day, 23, 59, 59, tzinfo=UK)
    return end_of_day.astimezone(UTC)


def prematch_odds_available_at(kickoff: datetime) -> datetime:
    """Provider collects pre-match odds on Friday (weekend games) or Tuesday (midweek)."""
    local = kickoff.astimezone(UK)
    weekday = local.weekday()  # Mon=0
    back_days = {4: 0, 5: 1, 6: 2, 0: 3, 1: 0, 2: 1, 3: 2}[weekday]
    collect_day = local.date() - timedelta(days=back_days)
    collected = datetime(collect_day.year, collect_day.month, collect_day.day,
                         ODDS_COLLECTION_HOUR, 0, tzinfo=UK).astimezone(UTC)
    return min(collected, kickoff - timedelta(hours=1))


def parse_csv(
    raw: RawFile,
    registry: TeamRegistry,
    *,
    competition: str | None = None,
    season_label: str | None = None,
    known_competitions: Iterable[str] | None = None,
) -> ParsedFile:
    """Parse a season file (``competition``/``season_label`` given) or fixtures.csv."""
    text = _decode(raw.content)
    reader = csv.reader(io.StringIO(text))
    out = ParsedFile(report=QualityReport(SOURCE, raw.uri))
    report = out.report
    assert report is not None
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        report.add("reject", "EMPTY_FILE", "no header row", raw.uri)
        return out
    allowed = set(known_competitions) if known_competitions is not None else None
    seen: set[str] = set()
    known_before = set(registry.display_names)
    for line_no, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue
        ref = f"{raw.uri}#L{line_no}"
        report.rows_read += 1
        if len(cells) > len(header) and any(c.strip() for c in cells[len(header):]):
            report.add("warning", "EXTRA_CELLS", f"{len(cells)} cells for {len(header)} columns",
                       ref)
        row = dict(zip(header, cells, strict=False))
        comp = (row.get("Div") or competition or "").strip()
        if competition and comp != competition:
            report.add("quarantine", "DIVISION_MISMATCH", f"Div={comp!r}", ref)
            continue
        if allowed is not None and comp not in allowed:
            continue  # fixtures.csv lists many leagues; silently keep only configured ones
        home_name = (row.get("HomeTeam") or row.get("HT") or "").strip()
        away_name = (row.get("AwayTeam") or row.get("AT") or "").strip()
        match_date = _parse_date(row.get("Date", ""))
        if not home_name or not away_name or match_date is None:
            report.add("reject", "MISSING_IDENTITY", f"home={home_name!r} away={away_name!r} "
                       f"date={row.get('Date')!r}", ref)
            continue
        if home_name == away_name:
            report.add("reject", "SAME_TEAM", home_name, ref)
            continue
        season = season_label or season_for_date(match_date)
        lo, hi = season_bounds(season)
        if not lo <= match_date <= hi:
            report.add("quarantine", "DATE_OUTSIDE_SEASON", f"{match_date} not in {season}", ref)
            continue
        hg = _parse_int(row.get("FTHG", row.get("HG")))
        ag = _parse_int(row.get("FTAG", row.get("AG")))
        problem = check_score(hg, ag, (row.get("FTR") or row.get("Res") or "").strip() or None)
        if problem:
            report.add(problem[0], problem[1], problem[2], ref)
            continue
        home_id = registry.register_source_name(home_name)
        away_id = registry.register_source_name(away_name)
        out.teams.setdefault(home_id, home_name)
        out.teams.setdefault(away_id, away_name)
        # The date is part of the ID: some leagues (e.g. Scotland) repeat home/away pairs.
        fixture_id = fixture_key(comp, match_date, home_id, away_id)
        if fixture_id in seen:
            report.add("quarantine", "DUPLICATE_FIXTURE", fixture_id, ref)
            continue
        seen.add(fixture_id)
        ko, time_known = kickoff_utc(match_date, row.get("Time"))
        finished = hg is not None and ag is not None
        out.matches.append(
            {
                "fixture_id": fixture_id,
                "competition": comp,
                "season": season,
                "home_team": home_id,
                "away_team": away_id,
                "kickoff_utc": ko,
                "kickoff_time_known": time_known,
                "status": (FixtureStatus.FINISHED if finished else FixtureStatus.SCHEDULED).value,
                "home_goals": hg,
                "away_goals": ag,
                "result_available_at": result_available_at(ko, time_known) if finished else None,
                "source_ref": ref,
            }
        )
        report.rows_accepted += 1
        pre = _first_odds(row, PREMATCH_1X2)
        if pre and not finished and ko <= raw.retrieved_at:
            pre = None  # unplayed-in-file but already kicked off: odds time is unknowable
        if pre:
            ou = _first_odds(row, PREMATCH_OU)
            out.odds.append(
                {"fixture_id": fixture_id, "odds_type": "prematch", "bookmaker": pre[3],
                 # Played match: provider's collection schedule. Upcoming fixture: the
                 # odds were observed when we downloaded the file.
                 "available_at": prematch_odds_available_at(ko) if finished
                 else min(raw.retrieved_at, ko),
                 "availability_basis": "inferred" if finished else "observed", "home": pre[0],
                 "draw": pre[1], "away": pre[2], "over25": ou[0] if ou else None,
                 "under25": ou[1] if ou else None}
            )
        close = _first_odds(row, CLOSING_1X2)
        if close:
            ou = _first_odds(row, CLOSING_OU)
            out.odds.append(
                {"fixture_id": fixture_id, "odds_type": "closing", "bookmaker": close[3],
                 "available_at": ko, "availability_basis": "kickoff", "home": close[0],
                 "draw": close[1], "away": close[2],
                 "over25": ou[0] if ou else None, "under25": ou[1] if ou else None}
            )
        if finished:
            stats = {k: _parse_float(row.get(col)) for k, col in STATS.items()}
            if any(v is not None for v in stats.values()):
                out.stats.append({"fixture_id": fixture_id,
                                  "available_at": result_available_at(ko, time_known), **stats})
    out.display_names = {t: registry.name(t) for t in out.teams}
    _flag_possible_aliases(out, known_before, report, raw.uri)
    return out


def fixture_key(competition: str, match_date: date, home_id: str, away_id: str) -> str:
    return f"{competition}-{match_date:%Y%m%d}-{home_id}-{away_id}"


def _flag_possible_aliases(out: ParsedFile, known_before: set[str], report: QualityReport,
                           uri: str) -> None:
    """Warn when a never-seen team shares its first word with another team in the file.

    Catches provider renames such as "Sheffield Utd" appearing beside "Sheffield United".
    These are only warnings: merging teams requires a reviewed alias in team_aliases.yaml.
    """
    counts: dict[str, int] = {}
    for m in out.matches:
        for t in (m["home_team"], m["away_team"]):
            counts[t] = counts.get(t, 0) + 1
    for team in sorted(set(out.teams) - known_before):
        stem = team.split("-")[0]
        similar = sorted(t for t in out.teams if t != team and t.split("-")[0] == stem)
        if similar:
            report.add("warning", "POSSIBLE_DUPLICATE_TEAM",
                       f"new team {out.teams[team]!r} ({counts.get(team, 0)} matches) resembles "
                       f"{', '.join(similar)}; add an alias if they are the same club", uri)
