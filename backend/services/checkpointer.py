"""Postgres-backed checkpointer for the healing graph.

Built around the SAME lifespan-owned pool db.py already manages — never
`AsyncPostgresSaver.from_conn_string()`, which is an async context manager
that closes its connection on block exit and therefore cannot back a
checkpointer that must survive across HTTP requests (the exact finding from
Phase 0 this phase depends on). Confirmed directly against
langgraph-checkpoint-postgres 3.1.2 before writing this: `AsyncPostgresSaver`
accepts either a bare `AsyncConnection` or an `AsyncConnectionPool`; passed a
pool, every internal cursor operation checks a connection out and back in via
`pool.connection()`, and every cursor is opened with its own explicit
`row_factory=dict_row` regardless of the pool's configured default — so
there is no conflict with db.py's pool-wide `dict_row` setting either way.
"""

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from db import get_pool

_saver: AsyncPostgresSaver | None = None


def get_checkpointer() -> AsyncPostgresSaver:
    global _saver
    if _saver is None:
        _saver = AsyncPostgresSaver(get_pool())
    return _saver


async def setup_checkpointer() -> None:
    """Creates/migrates the checkpoint tables. Idempotent (version-tracked
    via its own `checkpoint_migrations` table) — safe to call on every
    startup, same pattern as db.init_schema() / store.init_mapping_table().
    """
    await get_checkpointer().setup()
