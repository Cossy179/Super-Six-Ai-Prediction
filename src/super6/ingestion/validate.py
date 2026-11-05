"""Record-level validation and quality reports (DESIGN.md §8.5).

* ``reject``: the row cannot be interpreted (no teams, no date).
* ``quarantine``: the row parses but is suspicious (impossible score, inconsistent result
  code, duplicate fixture, date outside the season). It is recorded, not used.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Severity = Literal["reject", "quarantine", "warning"]
MAX_PLAUSIBLE_GOALS = 15


@dataclass(frozen=True)
class QualityIssue:
    severity: Severity
    code: str
    detail: str
    record_ref: str


@dataclass
class QualityReport:
    source: str
    uri: str
    rows_read: int = 0
    rows_accepted: int = 0
    issues: list[QualityIssue] = field(default_factory=list)

    def add(self, severity: Severity, code: str, detail: str, record_ref: str) -> None:
        self.issues.append(QualityIssue(severity, code, detail, record_ref))

    @property
    def status(self) -> str:
        if self.rows_accepted == 0:
            return "failed"
        if any(i.severity in ("reject", "quarantine") for i in self.issues):
            return "issues"
        return "ok"

    def summary(self) -> dict[str, int]:
        return dict(Counter(f"{i.severity}:{i.code}" for i in self.issues))


def season_bounds(season_label: str) -> tuple[date, date]:
    """Plausible match dates for a season label ('2023-24').

    Wide enough for the COVID-delayed 2019-20 finish (play-offs in August 2020).
    """
    start_year = int(season_label[:4])
    return date(start_year, 6, 1), date(start_year + 1, 8, 31)


def check_score(home_goals: int | None, away_goals: int | None, result_code: str | None
                ) -> tuple[Severity, str, str] | None:
    """Validate a full-time score. Returns (severity, code, detail) for a problem."""
    if home_goals is None and away_goals is None:
        return None  # unplayed fixture
    if home_goals is None or away_goals is None:
        return "quarantine", "PARTIAL_SCORE", f"home={home_goals} away={away_goals}"
    if home_goals < 0 or away_goals < 0:
        return "quarantine", "NEGATIVE_SCORE", f"{home_goals}-{away_goals}"
    if home_goals > MAX_PLAUSIBLE_GOALS or away_goals > MAX_PLAUSIBLE_GOALS:
        return "quarantine", "IMPLAUSIBLE_SCORE", f"{home_goals}-{away_goals}"
    if result_code:
        expected = "H" if home_goals > away_goals else "A" if home_goals < away_goals else "D"
        if result_code != expected:
            return ("quarantine", "RESULT_CODE_MISMATCH",
                    f"{home_goals}-{away_goals} recorded as {result_code}")
    return None
