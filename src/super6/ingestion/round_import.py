"""Assisted import of a Super 6 round from the six matchups a person copies from the app.

The official fixture list sits behind the player's login, and there is no authorised
API. So the person supplies the six pairings, which also serves as the manual verification
step DESIGN.md asks for. Everything else is filled in automatically:

* team names are resolved through the alias registry (unknown names fail with
  suggestions; nothing is fuzzy-matched silently);
* competition and kick-off time come from the data provider's fixture list when the
  match is listed. Otherwise the competition is inferred from the teams' current league
  and the kick-off is taken from the text, or assumed to be 15:00 with a warning;
* the deadline is the first kick-off ("Each Round closes at the kick off time of the
  first fixture of each round").

Accepted text: one matchup per line, or separated by commas or semicolons, for example
``Fulham v Newcastle``, ``2. Man Utd vs Aston Villa 17:30``, or
``Wrexham - Orient (E1)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
import yaml

from super6.domain import FixtureStatus
from super6.features.point_in_time import MatchHistory
from super6.ingestion.canonicalize import TeamRegistry, UnknownTeamError
from super6.timeutil import UK, iso

_DAY = r"(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*"
_TIME = r"\d{1,2}[:.]\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm)"
# Commas separate matchups, except a comma that introduces a day or time
# ("Crystal Palace vs Liverpool, Sunday 2pm").
_SPLIT = re.compile(rf"[\n;]+|,(?!\s*(?:{_DAY}\b|(?:{_TIME})\b))", re.IGNORECASE)
_PAIR = re.compile(
    r"^\s*(?:\d+\s*[.)]\s*)?(?P<home>.+?)\s+(?:v|vs|v\.|vs\.|versus|-|–|—)\s+(?P<away>.+?)"
    r"(?:\s*\((?P<comp>[A-Z]{1,3}\d?)\))?"
    rf"(?:\s*,?\s*(?P<day>{_DAY}))?(?:\s*,?\s*(?P<time>{_TIME}))?"
    r"(?:\s*\((?P<comp2>[A-Z]{1,3}\d?)\))?\s*$",
    re.IGNORECASE,
)
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DEFAULT_LOCAL_KICKOFF = (15, 0)
MATCH_WINDOW_DAYS = 3


class RoundImportError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class ParsedPair:
    home: str
    away: str
    local_time: tuple[int, int] | None
    competition: str | None
    text: str
    weekday: int | None = None


@dataclass(frozen=True)
class ImportedFixture:
    home_team: str
    away_team: str
    competition: str
    kickoff_utc: datetime
    source: str  # provider_fixture_list | text (time given) | assumed (15:00 default)


@dataclass
class RoundImport:
    round_id: str
    deadline_utc: datetime
    fixtures: list[ImportedFixture]
    warnings: list[str] = field(default_factory=list)

    def to_spec_dict(self, rules_version: str, note: str, confirmed: bool = True
                     ) -> dict[str, Any]:
        return {
            "round_id": self.round_id,
            "illustrative": False,
            "confirmed": confirmed,
            "source_note": note,
            "deadline_utc": iso(self.deadline_utc),
            "rules_version": rules_version,
            "fixtures": [{"home": f.home_team, "away": f.away_team, "competition": f.competition,
                          "kickoff_utc": iso(f.kickoff_utc)} for f in self.fixtures],
        }

    def to_yaml(self, rules_version: str, note: str, confirmed: bool = True) -> str:
        header = ("# Imported with `super6 round import`. Check it against the Super 6 app\n"
                  "# before relying on it, especially kick-off times marked as assumed.\n")
        return header + yaml.safe_dump(self.to_spec_dict(rules_version, note, confirmed),
                                       sort_keys=False)


def parse_fixture_text(text: str) -> list[ParsedPair]:
    pairs, bad = [], []
    for chunk in _SPLIT.split(text):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _PAIR.match(chunk)
        if not m:
            bad.append(chunk)
            continue
        local = _parse_time(m.group("time")) if m.group("time") else None
        day = m.group("day")
        comp = m.group("comp") or m.group("comp2")
        pairs.append(ParsedPair(m.group("home").strip(), m.group("away").strip(), local,
                                comp.upper() if comp else None, chunk,
                                _WEEKDAYS[day[:3].lower()] if day else None))
    if bad:
        raise RoundImportError([f"could not read {b!r} (expected 'Home v Away')" for b in bad])
    return pairs


def _parse_time(text: str) -> tuple[int, int]:
    t = text.strip().lower().replace(" ", "")
    suffix = t[-2:] if t.endswith(("am", "pm")) else ""
    t = t[: len(t) - len(suffix)]
    hh, mm = [*re.split(r"[:.]", t), "0"][:2]
    hour, minute = int(hh), int(mm)
    if suffix == "pm" and hour < 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise RoundImportError([f"invalid kick-off time {text!r}"])
    return hour, minute


def _match_date(round_date: date, weekday: int | None) -> date | None:
    """The date with ``weekday`` from the day before ``round_date`` to three days after."""
    if weekday is None:
        return round_date
    for offset in range(-1, MATCH_WINDOW_DAYS + 1):
        d = round_date + timedelta(days=offset)
        if d.weekday() == weekday:
            return d
    return None


def next_saturday(today: date) -> date:
    return today + timedelta(days=(5 - today.weekday()) % 7)


def _current_competition(results: pd.DataFrame, team: str) -> str | None:
    played = results.loc[(results["home_team"] == team) | (results["away_team"] == team)]
    if played.empty:
        return None
    return str(played.sort_values("kickoff_utc")["competition"].iloc[-1])


def import_round(
    pairs: list[ParsedPair],
    registry: TeamRegistry,
    history: MatchHistory,
    round_date: date,
    *,
    round_number: int | None = None,
    known_competitions: set[str] | None = None,
) -> RoundImport:
    problems: list[str] = []
    warnings: list[str] = []
    m = history.matches
    start = pd.Timestamp(datetime(round_date.year, round_date.month, round_date.day,
                                  tzinfo=UK)).tz_convert(UTC) - pd.Timedelta(days=1)
    end = start + pd.Timedelta(days=MATCH_WINDOW_DAYS + 1)
    scheduled = m.loc[(m["status"] == FixtureStatus.SCHEDULED.value)
                      & (m["kickoff_utc"] >= start) & (m["kickoff_utc"] < end)]
    if "fixture_source" in scheduled.columns:
        # Fixtures we created from an earlier round file are not provider evidence.
        scheduled = scheduled.loc[scheduled["fixture_source"] != "round_file"]
    finished = m.loc[m["status"] == FixtureStatus.FINISHED.value]
    out: list[ImportedFixture] = []
    for p in pairs:
        try:
            home, away = registry.resolve(p.home), registry.resolve(p.away)
        except UnknownTeamError as exc:
            problems.append(str(exc.args[0]))
            continue
        listed = scheduled.loc[(scheduled["home_team"] == home)
                               & (scheduled["away_team"] == away)]
        if len(listed) == 1:
            row = listed.iloc[0]
            kickoff = row["kickoff_utc"].to_pydatetime()
            comp = str(row["competition"])
            if p.local_time and kickoff.astimezone(UK).timetuple()[3:5] != p.local_time:
                warnings.append(f"{p.text}: you gave {p.local_time[0]:02d}:{p.local_time[1]:02d} "
                                f"but the provider lists {kickoff.astimezone(UK):%H:%M} (UK); "
                                "using the provider's time")
            if p.competition and p.competition != comp:
                warnings.append(f"{p.text}: provider lists it in {comp}, not {p.competition}")
            out.append(ImportedFixture(home, away, comp, kickoff, "provider_fixture_list"))
            continue
        inferred = p.competition
        if inferred is None:
            ch, ca = _current_competition(finished, home), _current_competition(finished, away)
            if ch is None or ch != ca:
                problems.append(f"{p.text}: can't tell the competition ({home}: {ch}, {away}: "
                                f"{ca}); add it in brackets, e.g. '{p.text} (E1)'")
                continue
            inferred = ch
        if known_competitions is not None and inferred not in known_competitions:
            problems.append(f"{p.text}: competition {inferred} is not configured in leagues.yaml")
            continue
        hh, mm = p.local_time or DEFAULT_LOCAL_KICKOFF
        match_day = _match_date(round_date, p.weekday)
        if match_day is None:
            problems.append(f"{p.text}: that day is outside the round weekend")
            continue
        if p.local_time is None:
            warnings.append(f"KICKOFF_ASSUMED: {p.text} is not in the provider's fixture list "
                            "yet; assumed 15:00 UK. Check it in the app")
        local = datetime(match_day.year, match_day.month, match_day.day, hh, mm, tzinfo=UK)
        out.append(ImportedFixture(home, away, inferred, local.astimezone(UTC),
                                   "text" if p.local_time else "assumed"))
    if problems:
        raise RoundImportError(problems)
    deadline = min(f.kickoff_utc for f in out)
    season_start = round_date.year if round_date.month >= 7 else round_date.year - 1
    season = f"{season_start}-{(season_start + 1) % 100:02d}"
    round_id = (f"super6-{season}-round-{round_number:02d}" if round_number
                else f"super6-{round_date:%Y-%m-%d}")
    return RoundImport(round_id, deadline, out, warnings)
