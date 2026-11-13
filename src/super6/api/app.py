"""The FastAPI application. Run with ``super6 serve``."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from super6 import __version__
from super6.api.deps import ApiContext
from super6.api.queries import NotFoundError
from super6.api.routes import admin, public

DESCRIPTION = """\
Forecasts for the six fixtures in a Super 6 round, both ways of picking scores, and the
Golden Goal minute.

- **Forecasts are estimates, never guarantees.** Even the most likely exact score usually
  has only a 10–15% chance.
- A response with `is_locked: false` can still change. Only a locked run is the record of
  what was forecast before the deadline.
- Nothing here submits an entry: picks are entered by hand on the official site.
- This is an independent analysis tool. It is not affiliated with or endorsed by Sky.
"""


def create_app(ctx: ApiContext | None = None) -> FastAPI:
    ctx = ctx or ApiContext.from_env()
    app = FastAPI(title="Super 6 AI", version=__version__, description=DESCRIPTION,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.ctx = ctx
    if ctx.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=ctx.cors_origins,
                           allow_methods=["GET", "POST"],
                           allow_headers=["Authorization", "Content-Type"])

    @app.exception_handler(NotFoundError)
    def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {"service": "super6-ai", "version": __version__, "docs": "/api/docs",
                "health": "/api/v1/health"}

    app.include_router(public.router)
    app.include_router(admin.router)
    return app
