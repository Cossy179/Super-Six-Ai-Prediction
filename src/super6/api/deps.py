"""What the API needs from its surroundings, and who may call the admin endpoints."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from super6.features.point_in_time import MatchHistory
from super6.models.base import ScoreModel
from super6.rules import CompetitionRules
from super6.storage.db import session_scope

ADMIN_TOKEN_ENV = "SUPER6_ADMIN_TOKEN"
CORS_ENV = "SUPER6_CORS_ORIGINS"
MIN_TOKEN_LENGTH = 24
# After this many wrong tokens in the window, admin calls are refused for the rest of it.
MAX_FAILED_AUTH = 10
FAILED_AUTH_WINDOW_SECONDS = 300.0


@dataclass
class ApiContext:
    engine: Engine
    rules: CompetitionRules
    rounds_dir: Path
    reports_dir: Path
    known_competitions: set[str]
    model_defaults: Mapping[str, Any]
    load_history: Callable[[Session], MatchHistory]
    model_factory: Callable[[], ScoreModel]
    # Downloads the provider's upcoming fixtures and odds; None disables `refresh_data`.
    refresh: Callable[[Session], list[str]] | None = None
    admin_token: str | None = None
    cors_origins: list[str] = field(default_factory=list)
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    # One forecast job at a time, so two calls cannot both save a "new" run.
    generate_lock: threading.Lock = field(default_factory=threading.Lock)
    _failed_auth: list[float] = field(default_factory=list)
    _auth_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def admin_enabled(self) -> bool:
        return self.admin_token is not None

    @classmethod
    def from_env(cls) -> ApiContext:
        from super6.config import Settings, project_root
        from super6.ingestion import pipeline
        from super6.models import build_model, load_model_defaults
        from super6.rules import load_rules
        from super6.storage.db import make_engine
        from super6.storage.repositories import load_history

        settings = Settings.from_env()
        defaults = load_model_defaults()
        token = os.environ.get(ADMIN_TOKEN_ENV) or None
        if token is not None and len(token) < MIN_TOKEN_LENGTH:
            raise RuntimeError(f"{ADMIN_TOKEN_ENV} must be at least {MIN_TOKEN_LENGTH} "
                               "characters; generate one with `super6 admin-token`")
        origins = [o.strip() for o in os.environ.get(CORS_ENV, "").split(",") if o.strip()]

        def refresh(session: Session) -> list[str]:
            msgs: list[str] = []
            pipeline.ingest_upcoming(session, settings.data_dir / "raw", log=msgs.append)
            return msgs

        return cls(
            engine=make_engine(settings.database_url), rules=load_rules(),
            rounds_dir=project_root() / "rounds", reports_dir=settings.reports_dir,
            known_competitions=set(pipeline.leagues_config()["competitions"]),
            model_defaults=defaults, load_history=load_history,
            model_factory=lambda: build_model(defaults["default_model"], defaults=defaults),
            refresh=refresh, admin_token=token, cors_origins=origins)

    # ----------------------------------------------------------------- admin access
    def check_admin(self, presented: str | None) -> None:
        """Raise unless ``presented`` is the admin token. Wrong guesses are throttled."""
        if self.admin_token is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                f"admin endpoints are disabled: {ADMIN_TOKEN_ENV} is not set")
        now = time.monotonic()
        with self._auth_lock:
            self._failed_auth[:] = [t for t in self._failed_auth
                                    if now - t < FAILED_AUTH_WINDOW_SECONDS]
            if len(self._failed_auth) >= MAX_FAILED_AUTH:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                    "too many failed admin attempts; try again later")
            ok = presented is not None and secrets.compare_digest(
                presented.encode(), self.admin_token.encode())
            if not ok:
                self._failed_auth.append(now)
        if not ok:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "admin token missing or wrong",
                                headers={"WWW-Authenticate": "Bearer"})

    def audit(self, action: str, **fields: Any) -> None:
        """Append a privileged change to ``reports/admin_audit.jsonl`` (no personal data)."""
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        record = {"at": self.now().isoformat(), "action": action, **fields}
        with (self.reports_dir / "admin_audit.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")


def get_context(request: Request) -> ApiContext:
    ctx = request.app.state.ctx
    assert isinstance(ctx, ApiContext)
    return ctx


Context = Annotated[ApiContext, Depends(get_context)]


def get_session(ctx: Context) -> Iterator[Session]:
    with session_scope(ctx.engine) as session:
        yield session


Db = Annotated[Session, Depends(get_session)]


def require_admin(ctx: Context, authorization: Annotated[str | None, Header()] = None) -> None:
    scheme, _, token = (authorization or "").partition(" ")
    ctx.check_admin(token.strip() if scheme.lower() == "bearer" and token else None)


Admin = Depends(require_admin)
