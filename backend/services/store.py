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

Every row carries a `pipeline_env` namespace and every read/write filters on
it. Without this, a verification script sharing the same DATABASE_URL as the
deployed app — which scripts/check_pipeline.py does, deliberately, to prove
persistence is real — would leave its test patches as the "current" mapping
the live app reads. That is silent and expensive to debug: once an agent is
actually writing patches during a run (Phase 3+), a stray test execution
could leave day1_clean failing or day2 passing on the first run with no
healing, and it would look exactly like a broken agent.
"""

from pathlib import Path

import yaml
from psycopg.types.json import Json

from config import get_settings
from db import get_pool
from pipeline.mapping import MappingPatch

SEED_PATH = Path(__file__).resolve().parents[2] / "config" / "mapping.yaml"


def _env(pipeline_env: str | None) -> str:
    return pipeline_env if pipeline_env is not None else get_settings().pipeline_env


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
        # Idempotent migration: a mapping_state table created before the
        # pipeline_env column existed gets one, defaulted to "default" so its
        # existing rows keep being what the real app reads.
        await conn.execute(
            "alter table mapping_state "
            "add column if not exists pipeline_env text not null default 'default'"
        )
        await conn.execute(
            "create index if not exists idx_mapping_state_env_id "
            "on mapping_state (pipeline_env, id desc)"
        )


async def get_current_mapping(pipeline_env: str | None = None) -> MappingPatch:
    """The latest row in this namespace, or the seed file on a first-ever
    call for it (which also persists it, so the seed is read at most once per
    namespace).
    """
    env = _env(pipeline_env)
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "select mapping from mapping_state "
            "where pipeline_env = %s order by id desc limit 1",
            (env,),
        )
        row = await cur.fetchone()

    if row is not None:
        return MappingPatch.model_validate(row["mapping"])

    seed_raw = yaml.safe_load(SEED_PATH.read_text(encoding="utf-8")) or {}
    seed = MappingPatch.model_validate(seed_raw)
    await save_mapping(seed, updated_by="seed", pipeline_env=env)
    return seed


async def save_mapping(
    mapping: MappingPatch, updated_by: str, pipeline_env: str | None = None
) -> MappingPatch:
    """Validates (again — cheap, and callers should never skip it) and
    appends a new row in this namespace. Returns the mapping actually stored.
    """
    env = _env(pipeline_env)
    validated = MappingPatch.model_validate(mapping)
    async with get_pool().connection() as conn:
        await conn.execute(
            "insert into mapping_state (mapping, updated_by, pipeline_env) "
            "values (%s, %s, %s)",
            (Json(validated.model_dump()), updated_by, env),
        )
    return validated


async def mapping_history(limit: int = 20, pipeline_env: str | None = None) -> list[dict]:
    """Most recent snapshots first, in this namespace. Not used until the
    Phase 5 dashboard, but trivial given the append-only table, and worth
    having for manual verification (confirming a save actually landed as a
    new row).
    """
    env = _env(pipeline_env)
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "select id, mapping, updated_by, created_at, pipeline_env "
            "from mapping_state where pipeline_env = %s order by id desc limit %s",
            (env, limit),
        )
        return list(await cur.fetchall())
