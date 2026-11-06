"""Goal times from openfootball (https://github.com/openfootball), released under CC0 1.0.

The plain-text league files list each match with its scorers and minutes, for example::

    Fri Aug 15 2025
      19:00   Liverpool  4-2 (1-0)  Bournemouth
                      (Hugo EKITIKE 37', ... MOHAMED SALAH 90+4';
                       Antoine SEMENYO 64', 76')

From 2026-27 the files put the score after the teams (``Arsenal FC  v  Coventry City FC
3-0 (2-0)``); both layouts are read. Home goals come before the ``;`` and away goals
after it. Own goals (``(og)``) are listed
under the team they count for, so each side's count must equal its score. Matches where it
doesn't are rejected. For the Premier League, goal minutes are available from 2025-26.
Matches are joined to canonical fixtures by competition, date and teams.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from super6.ingestion.adapters.football_data_couk import fixture_key
from super6.ingestion.canonicalize import TeamRegistry, UnknownTeamError, normalise_name

SOURCE = "openfootball"
BASE_URL = "https://raw.githubusercontent.com/openfootball/england/master"
FILES = {"E0": "1-premierleague.txt", "E1": "2-championship.txt"}
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
_DATE = re.compile(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})"
                   r"(?:\s+(?P<year>\d{4}))?\s*$")
_MATCH = re.compile(r"^\s+(?:\d{1,2}[.:]\d{2}\s+)?(?P<home>\S.*?)\s+(?P<hg>\d+)-(?P<ag>\d+)"
                    r"(?:\s+\(\d+-\d+\))?\s+(?P<away>\S.*?)\s*$")
# Newer layout (2026-27 onwards): "20:00  Arsenal FC  v Coventry City FC  3-0 (2-0)".
_MATCH_V = re.compile(r"^\s+(?:\d{1,2}[.:]\d{2}\s+)?(?P<home>\S.*?)\s+v\s+(?P<away>\S.*?)\s+"
                      r"(?P<hg>\d+)-(?P<ag>\d+)(?:\s+\(\d+-\d+\))?\s*$")
_MINUTE = re.compile(r"(?P<min>\d{1,3})(?:\+(?P<add>\d{1,2}))?'")


def season_path(competition: str, season_label: str) -> str:
    return f"{BASE_URL}/{season_label}/{FILES[competition]}"


@dataclass(frozen=True)
class Goal:
    side: str  # "home" | "away" (the team the goal counts for)
    minute: int  # 1..90 on the match clock (stoppage goals carry their period's 45/90)
    stoppage: int | None  # e.g. 4 for "90+4'"

    @property
    def period(self) -> int:
        return 1 if self.minute <= 45 else 2


@dataclass
class ParsedMatch:
    match_date: date
    home: str
    away: str
    home_goals: int
    away_goals: int
    goals: list[Goal]


@dataclass
class ParsedGoals:
    matches: list[ParsedMatch] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    without_goal_times: int = 0  # scored matches whose scorers/minutes aren't listed yet


def _minutes(text: str, side: str) -> list[Goal]:
    out = []
    for m in _MINUTE.finditer(text):
        minute = int(m.group("min"))
        add = int(m.group("add")) if m.group("add") else None
        out.append(Goal(side, min(minute, 90), add))
    return out


def parse_league_text(text: str, first_year: int) -> ParsedGoals:
    """Matches with goal minutes. ``first_year`` dates entries before a year appears."""
    out = ParsedGoals()
    lines = text.splitlines()
    year, last_month = first_year, 0
    current: date | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        d = _DATE.match(line.strip())
        if d:
            month = _MONTHS[d.group("mon")]
            if d.group("year"):
                year = int(d.group("year"))
            elif month < last_month:
                year += 1  # the season crosses New Year
            last_month = month
            current = date(year, month, int(d.group("day")))
            i += 1
            continue
        m = _MATCH_V.match(line) or _MATCH.match(line)
        if m and current is not None and not line.strip().startswith(("(", "#", "▪")):
            goals_text = ""
            j = i + 1
            if j < len(lines) and lines[j].strip().startswith("("):
                buf = []
                while j < len(lines):
                    buf.append(lines[j].strip())
                    if lines[j].strip().endswith(")"):
                        break
                    j += 1
                goals_text = " ".join(buf)[1:-1]
                i = j
            hg, ag = int(m.group("hg")), int(m.group("ag"))
            if ";" in goals_text:
                home_text, _, away_text = goals_text.partition(";")
            elif hg == 0:  # only one side scored: the file omits the separator
                home_text, away_text = "", goals_text
            else:
                home_text, away_text = goals_text, ""
            goals = _minutes(home_text, "home") + _minutes(away_text, "away")
            pm = ParsedMatch(current, m.group("home").strip(), m.group("away").strip(),
                             hg, ag, goals)
            if not goals_text and hg + ag > 0:
                out.without_goal_times += 1  # not an error: goal details not published yet
                i += 1
                continue
            n_home = sum(g.side == "home" for g in goals)
            n_away = len(goals) - n_home
            if (n_home, n_away) != (pm.home_goals, pm.away_goals):
                out.problems.append(f"{current} {pm.home} v {pm.away}: goal list has "
                                    f"{n_home}-{n_away} but the score is "
                                    f"{pm.home_goals}-{pm.away_goals}")
            else:
                out.matches.append(pm)
        i += 1
    return out


def resolve_team(registry: TeamRegistry, name: str) -> str:
    """openfootball names, with a trailing " FC" or a leading "AFC" tolerated."""
    try:
        return registry.resolve(name)
    except UnknownTeamError:
        stripped = re.sub(r"^(?:afc)\s+|\s+(?:fc|afc)$", "", normalise_name(name))
        return registry.resolve(stripped)


def fixture_id_for(competition: str, match: ParsedMatch, registry: TeamRegistry) -> str:
    return fixture_key(competition, match.match_date, resolve_team(registry, match.home),
                       resolve_team(registry, match.away))
