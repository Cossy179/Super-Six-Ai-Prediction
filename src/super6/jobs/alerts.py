"""Alerts for the unattended round cycle (DESIGN.md §16.2).

Every alert is appended to ``reports/alerts.jsonl``. Warnings and errors are also posted
to ``SUPER6_ALERT_WEBHOOK_URL`` when it is set, as a JSON body with both ``text`` (Slack)
and ``content`` (Discord) fields. The same alert code for the same round is suppressed for
24 hours, so an hourly job does not repeat itself. A failed webhook is logged and never
stops the job.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx

Severity = Literal["info", "warning", "error"]
WEBHOOK_ENV = "SUPER6_ALERT_WEBHOOK_URL"
DEDUPE_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class Alert:
    severity: Severity
    code: str
    message: str
    round_id: str | None = None
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class AlertSink:
    def __init__(self, log_path: Path, webhook_url: str | None = None,
                 post: Callable[..., Any] | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.log_path = log_path
        self.webhook_url = webhook_url if webhook_url is not None else os.environ.get(WEBHOOK_ENV)
        self._post = post or (lambda url, payload: httpx.post(url, json=payload, timeout=10.0))
        self._now = now
        self.emitted: list[Alert] = []

    def _recent(self, code: str, round_id: str | None) -> bool:
        if not self.log_path.exists():
            return False
        horizon = self._now() - DEDUPE_WINDOW
        with self.log_path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (rec.get("code") == code and rec.get("round_id") == round_id
                        and datetime.fromisoformat(rec["at"]) >= horizon):
                    return True
        return False

    def emit(self, severity: Severity, code: str, message: str,
             round_id: str | None = None, dedupe: bool = True) -> Alert | None:
        if dedupe and self._recent(code, round_id):
            return None
        alert = Alert(severity, code, message, round_id, self._now().isoformat())
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(alert)) + "\n")
        self.emitted.append(alert)
        if self.webhook_url and severity in ("warning", "error"):
            text = f"[super6 {severity.upper()}] {code}: {message}"
            try:
                self._post(self.webhook_url, {"text": text, "content": text})
            except Exception as exc:  # alerting must never stop the job
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(asdict(Alert("warning", "WEBHOOK_FAILED", str(exc),
                                                      round_id, self._now().isoformat())))
                             + "\n")
        return alert

    @property
    def has_errors(self) -> bool:
        return any(a.severity == "error" for a in self.emitted)
