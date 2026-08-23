"""Postgres connection pool with a process-lifetime scope.

Why a long-lived pool rather than the more obvious
``AsyncPostgresSaver.from_conn_string(...)``: that helper is an *async context
manager* and closes its connection when the block exits. A checkpointer opened
that way per-request cannot resume a graph across HTTP requests, which is the
whole point of the Phase 4 human-in-the-loop gate. So the pool is owned by the
FastAPI lifespan and outlives any single request; from Phase 4 the checkpointer
is constructed around this same pool.

Two Neon-specific notes:

* The free-tier compute suspends when idle and the first connection can take
  well over 30s to wake it. So the pool is opened with ``wait=False`` — the app
  boots immediately and Render's health check passes while Neon is still waking.
* Connections are autocommit; every statement here is a one-shot DDL/DML.
"""

import logging
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None


async def open_pool(database_url: str) -> AsyncConnectionPool:
    """Create the process-wide pool. Does not block on the first connection."""
    global _pool
    if _pool is not None:
        return _pool

    _pool = AsyncConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=8,
        open=False,
        # Neon suspends idle computes; recycle rather than hand out a dead socket.
        max_idle=120.0,
        timeout=45.0,
        kwargs={"autocommit": True, "row_factory": dict_row},
    )
    # wait=False: do not block startup on a cold Neon compute.
    await _pool.open(wait=False)
    log.info("db: pool created (lazy open)")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("db: pool closed")


def get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("db pool not initialised; call open_pool() in lifespan")
    return _pool


async def init_schema() -> None:
    """Phase 0 table proving we can actually write to Neon.

    Real domain tables arrive in Phase 1; the graph checkpoint tables are
    created by the checkpointer's own setup() in Phase 4.
    """
    async with get_pool().connection() as conn:
        await conn.execute(
            """
            create table if not exists deploy_probe (
                id         bigserial primary key,
                note       text        not null,
                app_env    text        not null,
                created_at timestamptz not null default now()
            )
            """
        )
    log.info("db: schema ready")


async def write_probe(note: str, app_env: str) -> dict[str, Any]:
    """Insert one row and read back the current count.

    This is the Phase 0 proof that the *deployed* backend reaches Neon — a
    read-only SELECT would not prove write access.
    """
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "insert into deploy_probe (note, app_env) values (%s, %s) "
            "returning id, created_at",
            (note, app_env),
        )
        row = await cur.fetchone()
        cur = await conn.execute("select count(*) as n from deploy_probe")
        total = (await cur.fetchone())["n"]

    return {
        "inserted_id": row["id"],
        "inserted_at": row["created_at"].isoformat(),
        "total_rows": total,
    }


async def server_version() -> str:
    async with get_pool().connection() as conn:
        cur = await conn.execute("show server_version")
        return (await cur.fetchone())["server_version"]
