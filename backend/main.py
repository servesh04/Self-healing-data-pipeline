"""FastAPI entrypoint.

Phase 0 scope: prove the deployment path end to end — a public frontend calling
a public backend, bearer auth enforced, Neon reachable and writable. There is
deliberately no pipeline and no LangGraph here yet; see ARCHITECTURE.md.
"""

# ── Windows/psycopg event-loop note ──────────────────────────────────────────
# psycopg's async mode cannot run on Windows' default ProactorEventLoop, and
# uvicorn hard-codes that loop on win32 via a loop *factory* -- which means
# asyncio.set_event_loop_policy() has no effect. Local Windows dev must therefore
# go through run_local.py, which owns the loop and installs a selector one.
# The Linux container uses epoll and is unaffected. _check_event_loop() below
# turns the resulting failure into an explicit message instead of a 45s timeout.
import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

import db
from auth import require_token
from config import Settings, get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("app")


def _pkg_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not-installed"


def _check_event_loop() -> str | None:
    """Return an explanation if the running loop cannot support psycopg async."""
    if sys.platform != "win32":
        return None
    loop = asyncio.get_running_loop()
    if isinstance(loop, asyncio.SelectorEventLoop):
        return None
    return (
        f"{type(loop).__name__} cannot run psycopg in async mode. "
        "On Windows start the server with `python run_local.py` instead of "
        "`uvicorn main:app`. (The Linux container is unaffected.)"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("startup: env=%s", settings.app_env)

    loop_problem = _check_event_loop()
    if loop_problem:
        log.critical("startup: INCOMPATIBLE EVENT LOOP -- %s", loop_problem)

    await db.open_pool(settings.database_url)
    try:
        await db.init_schema()
    except Exception:
        # A cold Neon compute can miss the first statement. Do not crash the
        # app: /health must stay green so the platform does not kill the
        # instance, and /api/ping reports the real error on demand.
        log.exception("startup: init_schema failed (will retry on first /api/ping)")

    try:
        yield
    finally:
        await db.close_pool()
        log.info("shutdown: complete")


app = FastAPI(
    title="Self-Healing Data Pipeline",
    version="0.1.0-phase0",
    summary="Phase 0 deployment skeleton",
    lifespan=lifespan,
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Unauthenticated ──────────────────────────────────────────────────────────
@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness only — deliberately does not touch the database.

    Render polls this. If it depended on Neon, a suspended free-tier compute
    would look like an unhealthy deploy.
    """
    return {"ok": True, "phase": 0}


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {"service": "self-healing-data-pipeline", "phase": 0, "docs": "/docs"}


# ── Authenticated ────────────────────────────────────────────────────────────
@app.get("/api/ping", dependencies=[Depends(require_token)], tags=["meta"])
async def ping(settings: Settings = Depends(get_settings)) -> dict:
    """Authenticated round-trip that also proves Neon is writable.

    Returns the recorded LangGraph version so the Phase 0 Definition of Done is
    verifiable from the deployed service, not just from a local shell.
    """
    db_info: dict = {}
    db_error: str | None = None
    try:
        await db.init_schema()  # idempotent; covers a failed cold start above
        db_info = await db.write_probe("api-ping", settings.app_env)
        db_info["server_version"] = await db.server_version()
    except Exception as exc:
        db_error = f"{type(exc).__name__}: {exc}"
        log.exception("ping: database probe failed")

    return {
        "ok": db_error is None,
        "message": "pong",
        "phase": 0,
        "env": settings.app_env,
        "database": {"connected": db_error is None, **db_info}
        | ({"error": db_error} if db_error else {}),
        "versions": {
            "langgraph": _pkg_version("langgraph"),
            "langgraph_checkpoint_postgres": _pkg_version(
                "langgraph-checkpoint-postgres"
            ),
            "fastapi": _pkg_version("fastapi"),
            "pandera": _pkg_version("pandera"),
            "pandas": _pkg_version("pandas"),
            "python": sys.version.split()[0],
        },
    }
