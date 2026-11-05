"""Time helpers. Everything is UTC internally; local time is only for parsing and display."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pandas as pd

UK = ZoneInfo("Europe/London")


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime | str | pd.Timestamp) -> datetime:
    """Parse/normalise to an aware UTC ``datetime``. Naive inputs are rejected."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if value.tzinfo is None:
        raise ValueError(f"timestamp {value!r} has no timezone; use UTC (e.g. ...Z)")
    return value.astimezone(UTC)


def to_timestamp(value: datetime | str | pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(ensure_utc(value))


def iso(value: datetime | pd.Timestamp) -> str:
    return ensure_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")
