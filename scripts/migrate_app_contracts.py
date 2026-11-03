"""One-shot migration for the app-uploaded contract tables.

Same owner-role-only pattern as migrate_p5.py: production runs with a read-only
runtime role, so `ensure_schema()` must be applied once by an owner rather than
at API startup. Requires APP_CONTRACTS_MIGRATION_DATABASE_URL (falls back to
P5_MIGRATION_DATABASE_URL for convenience).

Creates:
  app_contracts          current spec + contract_version() hash per name
  app_contract_versions  append-only spec history, unique (name, version)

Does not touch config/contracts/, mapping_state, run_records or any checkpoint
table. Safe to re-run.
"""

import asyncio
import os
import selectors
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db
from config import get_settings
from services import contract_store


async def migrate() -> None:
    dsn = (
        os.environ.get("APP_CONTRACTS_MIGRATION_DATABASE_URL")
        or os.environ.get("P5_MIGRATION_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not dsn:
        try:
            dsn = get_settings().database_url
        except Exception as exc:
            raise RuntimeError(
                "No database URL found: specify APP_CONTRACTS_MIGRATION_DATABASE_URL, "
                "P5_MIGRATION_DATABASE_URL, or DATABASE_URL in .env"
            ) from exc
    await db.open_pool(dsn)
    try:
        await contract_store.ensure_schema()
    finally:
        await db.close_pool()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(
            migrate(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        asyncio.run(migrate())
    print("app contract tables migrated")
