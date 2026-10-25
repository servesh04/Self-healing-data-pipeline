"""One-shot schema migration under an owner role; never run at API startup.

Requires P5_MIGRATION_DATABASE_URL. Review target database before invoking.
"""

import asyncio
import os
import selectors
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db
from services import checkpointer, external_store, store


async def migrate() -> None:
    dsn = os.environ["P5_MIGRATION_DATABASE_URL"]
    await db.open_pool(dsn)
    try:
        await db.init_schema()
        await store.init_mapping_table()
        await store.init_runs_table()
        await store.init_component_tables()
        await external_store.init_external_tables()
        await checkpointer.setup_checkpointer()
    finally:
        await db.close_pool()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(migrate(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(migrate())
    print("P5 schema migrated")
