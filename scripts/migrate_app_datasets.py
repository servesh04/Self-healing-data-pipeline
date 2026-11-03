"""One-shot migration for the app-uploaded dataset table.

Creates:
  app_datasets: uploaded immutable dataset snapshots + SHA-256 digest
"""

import asyncio
import os
import selectors
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import db
from config import get_settings


async def migrate() -> None:
    dsn = (
        os.environ.get("APP_DATASETS_MIGRATION_DATABASE_URL")
        or os.environ.get("APP_CONTRACTS_MIGRATION_DATABASE_URL")
        or os.environ.get("P5_MIGRATION_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not dsn:
        try:
            dsn = get_settings().database_url
        except Exception as exc:
            raise RuntimeError(
                "No database URL found: specify APP_DATASETS_MIGRATION_DATABASE_URL, "
                "APP_CONTRACTS_MIGRATION_DATABASE_URL, P5_MIGRATION_DATABASE_URL, or DATABASE_URL in .env"
            ) from exc
    await db.open_pool(dsn)
    try:
        async with db.get_pool().connection() as conn:
            await conn.execute("""
                create table if not exists app_datasets (
                    id          text primary key,
                    filename    text not null,
                    sha256      text not null,
                    payload     bytea not null,
                    byte_size   integer not null,
                    row_count   integer not null,
                    columns     jsonb not null default '[]'::jsonb,
                    created_at  timestamptz not null default now()
                )
            """)
            await conn.execute(
                "create unique index if not exists app_datasets_content_identity "
                "on app_datasets (sha256, filename)"
            )
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
    print("app dataset table migrated")
