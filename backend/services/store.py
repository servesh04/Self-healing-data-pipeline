"""Mapping state, persisted in Postgres — never on the container filesystem.

Render's disk is ephemeral and wipes on every restart; config/mapping.yaml is
read exactly once, to seed an empty table, and never again. Every write is
validated against MappingPatch first — this is the point at which "the agent
emits config patches only" is actually enforced, no matter what proposed the
patch.

The table is append-only rather than a single row updated in place: each row
is a full mapping snapshot, so "apply_patch backs up the current mapping"
(ARCHITECTURE.md) falls out for free as the previous row, and the history is
already there for whatever the Phase 5 dashboard wants to show.
"""

from pathlib import Path

import yaml
from psycopg.types.json import Json

from db import get_pool
from pipeline.mapping import MappingPatch

SEED_PATH = Path(__file__).resolve().parents[2] / "config" / "mapping.yaml"


async def init_mapping_table() -> None:
    async with get_pool().connection() as conn:
        await conn.execute(
            """
            create table if not exists mapping_state (
                id         bigserial primary key,
                mapping    jsonb       not null,
                updated_by text        not null,
                created_at timestamptz not null default now()
            )
            """
        )


async def get_current_mapping() -> MappingPatch:
    """The latest row, or the seed file on a first-ever call (which also
    persists it, so the seed is read at most once per database).
    """
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "select mapping from mapping_state order by id desc limit 1"
        )
        row = await cur.fetchone()

    if row is not None:
        return MappingPatch.model_validate(row["mapping"])

    seed_raw = yaml.safe_load(SEED_PATH.read_text(encoding="utf-8")) or {}
    seed = MappingPatch.model_validate(seed_raw)
    await save_mapping(seed, updated_by="seed")
    return seed


async def save_mapping(mapping: MappingPatch, updated_by: str) -> MappingPatch:
    """Validates (again — cheap, and callers should never skip it) and
    appends a new row. Returns the mapping actually stored.
    """
    validated = MappingPatch.model_validate(mapping)
    async with get_pool().connection() as conn:
        await conn.execute(
            "insert into mapping_state (mapping, updated_by) values (%s, %s)",
            (Json(validated.model_dump()), updated_by),
        )
    return validated


async def mapping_history(limit: int = 20) -> list[dict]:
    """Most recent snapshots first. Not used until the Phase 5 dashboard, but
    trivial given the append-only table, and worth having for the manual
    Phase 1 verification (confirming a save actually landed as a new row).
    """
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "select id, mapping, updated_by, created_at "
            "from mapping_state order by id desc limit %s",
            (limit,),
        )
        return list(await cur.fetchall())
