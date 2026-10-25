"""Immutable external batch staging and idempotent bridge claims."""

import hashlib
import csv
from io import StringIO
import json
from psycopg.types.json import Json

from db import get_pool

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 5000


def _mapping_digest(mapping: dict) -> str:
    return hashlib.sha256(json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def init_external_tables() -> None:
    async with get_pool().connection() as conn:
        await conn.execute("""create table if not exists external_order_batches (
            feed_id text not null, batch_id text not null, payload bytea not null,
            sha256 text not null, row_count integer not null, agent_run_id text,
            created_at timestamptz not null default now(),
            dispatch_lease_until timestamptz,
            primary key (feed_id, batch_id))""")
        await conn.execute("""alter table external_order_batches
            add column if not exists dispatch_lease_until timestamptz""")
        await conn.execute("""create unique index if not exists
            external_order_batches_run_id on external_order_batches(agent_run_id)
            where agent_run_id is not null""")
        await conn.execute("""create or replace function protect_external_order_batch()
            returns trigger language plpgsql as $$
            begin
                if old.feed_id is distinct from new.feed_id or
                   old.batch_id is distinct from new.batch_id or
                   old.payload is distinct from new.payload or
                   old.sha256 is distinct from new.sha256 or
                   old.row_count is distinct from new.row_count or
                   (old.agent_run_id is not null and old.agent_run_id is distinct from new.agent_run_id)
                then raise exception 'external batch is immutable'; end if;
                return new;
            end $$""")
        await conn.execute("""do $$ begin
            if not exists (select 1 from pg_trigger where tgname = 'external_order_batch_immutable'
                and tgrelid = 'external_order_batches'::regclass) then
                create trigger external_order_batch_immutable before update
                    on external_order_batches for each row execute function protect_external_order_batch();
            end if;
        end $$""")
        await conn.execute("""create table if not exists external_order_releases (
            feed_id text not null, batch_id text not null, sha256 text not null,
            agent_run_id text not null, pipeline_env text not null,
            mapping_version bigint not null, mapping_sha256 text not null,
            row_count integer not null, contract_version text not null,
            released_at timestamptz not null default now(),
            primary key (feed_id, batch_id))""")
        await conn.execute("""create table if not exists published_order_batches (
            feed_id text not null, batch_id text not null, sha256 text not null,
            agent_run_id text not null, mapping_version bigint not null,
            row_count integer not null, published_at timestamptz not null default now(),
            primary key (feed_id, batch_id))""")
        await conn.execute("""create table if not exists published_orders (
            feed_id text not null, batch_id text not null, order_id bigint not null,
            customer_id bigint not null, region text not null, order_date date not null,
            revenue double precision not null,
            primary key (feed_id, batch_id, order_id))""")


async def prepare_run(feed_id: str, batch_id: str, proposed_run_id: str,
                      env: str, contract_name: str, contract_version: str,
                      source_path: str) -> dict:
    """Claim exactly one staged batch and atomically register its run/snapshot."""
    async with get_pool().connection() as conn:
        async with conn.transaction():
            cur = await conn.execute("""select sha256, row_count, agent_run_id,
                octet_length(payload) as byte_count
                from external_order_batches where feed_id = %s and batch_id = %s for update""",
                (feed_id, batch_id))
            row = await cur.fetchone()
            if row is None:
                raise ValueError("external batch not staged")
            if not 20 <= row["row_count"] <= MAX_ROWS or row["byte_count"] > MAX_BYTES:
                raise ValueError("external batch exceeds row or byte cap")
            cur = await conn.execute("""select payload from external_order_batches
                where feed_id = %s and batch_id = %s""", (feed_id, batch_id))
            payload = bytes((await cur.fetchone())["payload"])
            digest = hashlib.sha256(payload).hexdigest()
            if digest != row["sha256"]:
                raise ValueError("staged batch digest mismatch")
            try:
                reader = csv.reader(StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
                header = next(reader)
                actual_rows = sum(1 for _ in reader)
            except (UnicodeError, csv.Error, StopIteration) as exc:
                raise ValueError("staged batch is not valid UTF-8 CSV") from exc
            if not header or len(header) != len(set(header)) or actual_rows != row["row_count"]:
                raise ValueError("staged batch header or row count mismatch")
            run_id = row["agent_run_id"] or proposed_run_id
            created = row["agent_run_id"] is None
            if created:
                await conn.execute("""update external_order_batches set agent_run_id = %s
                    where feed_id = %s and batch_id = %s""", (run_id, feed_id, batch_id))
                ref = {"kind": "external_batch", "source_id": f"{feed_id}/{batch_id}",
                       "feed_id": feed_id, "batch_id": batch_id, "sha256": digest,
                       "row_count": row["row_count"]}
                await conn.execute("""insert into source_snapshots
                    (run_id, source_id, payload, sha256, row_count, profile_rows)
                    values (%s, %s, %s, %s, %s, %s)""",
                    (run_id, ref["source_id"], payload, digest, row["row_count"],
                     min(row["row_count"], 1000)))
                await conn.execute("""insert into run_records
                    (run_id, source_path, pipeline_env, contract_name, contract_version, source_ref, mapping_scope)
                    values (%s, %s, %s, %s, %s, %s, %s)""",
                    (run_id, source_path, env, contract_name, contract_version,
                     Json(ref), f"mapping:{env}:{contract_name}"))
            return {"run_id": run_id, "created": created, "payload": payload,
                    "sha256": digest, "row_count": row["row_count"]}


async def claim_dispatch(feed_id: str, batch_id: str, run_id: str) -> bool:
    """At most one active scheduler; an expired claim can recover a crash."""
    async with get_pool().connection() as conn:
        cur = await conn.execute("""update external_order_batches
            set dispatch_lease_until = now() + interval '60 seconds'
            where feed_id = %s and batch_id = %s and agent_run_id = %s
              and (dispatch_lease_until is null or dispatch_lease_until < now())
            returning agent_run_id""", (feed_id, batch_id, run_id))
        return await cur.fetchone() is not None


async def get_batch(feed_id: str, batch_id: str) -> dict | None:
    async with get_pool().connection() as conn:
        cur = await conn.execute("""select sha256, row_count, agent_run_id
            from external_order_batches where feed_id = %s and batch_id = %s""", (feed_id, batch_id))
        return await cur.fetchone()


async def version_survived_rollback(version_id: int, env: str, contract_name: str) -> bool:
    async with get_pool().connection() as conn:
        cur = await conn.execute("""select exists (
            select 1 from mapping_state where id = %s and pipeline_env = %s and contract_name = %s
        ) as present, exists (
            select 1 from mapping_state where restores_version_id = %s
                and pipeline_env = %s and contract_name = %s and event_type = 'rollback'
        ) as rolled_back""", (version_id, env, contract_name, version_id, env, contract_name))
        row = await cur.fetchone()
        return bool(row["present"] and not row["rolled_back"])


async def authorize_release(feed_id: str, batch_id: str, manifest: dict) -> None:
    """Persist the exact validated mapping and source identity once."""
    mapping_hash = _mapping_digest(manifest["mapping"])
    values = (feed_id, batch_id, manifest["sha256"], manifest["run_id"],
              manifest["pipeline_env"], manifest["mapping_version"], mapping_hash,
              manifest["row_count"], manifest["contract_version"])
    async with get_pool().connection() as conn:
        async with conn.transaction():
            await conn.execute("""insert into external_order_releases
                (feed_id, batch_id, sha256, agent_run_id, pipeline_env,
                 mapping_version, mapping_sha256, row_count, contract_version)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (feed_id, batch_id) do nothing""", values)
            cur = await conn.execute("""select sha256, agent_run_id, pipeline_env,
                mapping_version, mapping_sha256, row_count, contract_version
                from external_order_releases where feed_id = %s and batch_id = %s""",
                (feed_id, batch_id))
            row = await cur.fetchone()
            if tuple(row[key] for key in ("sha256", "agent_run_id", "pipeline_env",
                                          "mapping_version", "mapping_sha256", "row_count",
                                          "contract_version")) != values[2:]:
                raise ValueError("release conflicts with prior authorization")
