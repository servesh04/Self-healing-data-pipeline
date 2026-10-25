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
from pipeline.contract import Contract, get_registry

SEED_PATH = Path(__file__).resolve().parents[2] / "config" / "mapping.yaml"


async def verify_runtime_schema() -> None:
    """Production startup is read-only; migrations run under an owner role."""
    required = ("mapping_state", "mapping_current", "run_records", "source_snapshots",
                "component_events", "component_labels", "approval_decisions",
                "external_order_batches", "external_order_releases",
                "published_order_batches", "published_orders",
                "checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations")
    async with get_pool().connection() as conn:
        for table in required:
            cur = await conn.execute("select to_regclass(%s) as relation", (f"public.{table}",))
            if (await cur.fetchone())["relation"] is None:
                raise RuntimeError(f"production schema missing {table}; run migrations as owner")


def _env(pipeline_env: str | None) -> str:
    return pipeline_env if pipeline_env is not None else get_settings().pipeline_env


def _contract(name: str) -> Contract:
    return get_registry().get(name)


def _scope(env: str, name: str) -> str:
    return f"mapping:{env}:{name}"


def _validated(mapping: MappingPatch | dict, name: str, contract: Contract | None = None) -> MappingPatch:
    selected = contract or _contract(name)
    if selected.name != name:
        raise ValueError("contract scope and pinned snapshot disagree")
    raw = mapping.model_dump() if isinstance(mapping, MappingPatch) else mapping
    return MappingPatch.model_validate(raw, context={"contract": selected})


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
        # Phase 5: which run caused this patch, so the run detail view can
        # show a per-run audit trail instead of the whole namespace's history
        # (which mixes every run that ever touched this mapping together).
        # Nullable: the seed row and anything saved outside a run have none.
        await conn.execute(
            "alter table mapping_state add column if not exists run_id text"
        )
        await conn.execute("alter table mapping_state add column if not exists contract_name text not null default 'default'")
        await conn.execute("alter table mapping_state add column if not exists parent_id bigint references mapping_state(id)")
        await conn.execute("alter table mapping_state add column if not exists event_type text not null default 'snapshot'")
        await conn.execute("alter table mapping_state add column if not exists restores_version_id bigint references mapping_state(id)")
        await conn.execute("alter table mapping_state add column if not exists rollback_reason text")
        await conn.execute("""with predecessors as (
            select id, lag(id) over (partition by pipeline_env, contract_name order by id) as previous_id
            from mapping_state)
            update mapping_state s set parent_id = p.previous_id from predecessors p
            where s.id = p.id and s.parent_id is null and p.previous_id is not null""")
        await conn.execute("create table if not exists mapping_current (pipeline_env text not null, contract_name text not null, current_version_id bigint not null references mapping_state(id), primary key (pipeline_env, contract_name))")
        # Legacy latest-row semantics migrate once. ON CONFLICT preserves an
        # existing pointer, including one that intentionally points to a
        # restoring version while later audit rows are retained.
        await conn.execute("""insert into mapping_current (pipeline_env, contract_name, current_version_id)
            select distinct on (pipeline_env, contract_name) pipeline_env, contract_name, id
            from mapping_state order by pipeline_env, contract_name, id desc
            on conflict (pipeline_env, contract_name) do nothing""")
        await conn.execute(
            "create index if not exists idx_mapping_state_env_id "
            "on mapping_state (pipeline_env, id desc)"
        )
        await conn.execute(
            "create index if not exists idx_mapping_state_run "
            "on mapping_state (run_id) where run_id is not null"
        )


async def get_current_mapping(pipeline_env: str | None = None, contract_name: str = "default",
                              contract: Contract | None = None) -> MappingPatch:
    """The latest row in this namespace, or the seed file on a first-ever
    call for it (which also persists it, so the seed is read at most once per
    namespace).
    """
    mapping, _ = await get_current_mapping_version(pipeline_env, contract_name, contract)
    return mapping


async def get_current_mapping_version(pipeline_env: str | None = None, contract_name: str = "default",
                                      contract: Contract | None = None) -> tuple[MappingPatch, int]:
    """Read the current snapshot and its ID for a guarded write."""
    env = _env(pipeline_env)
    contract = contract or _contract(contract_name)
    if contract.name != contract_name:
        raise ValueError("contract scope and pinned snapshot disagree")
    async with get_pool().connection() as conn:
        async with conn.transaction():
            await conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (_scope(env, contract_name),))
            cur = await conn.execute("""select s.id, s.mapping from mapping_current c
                join mapping_state s on s.id = c.current_version_id
                where c.pipeline_env = %s and c.contract_name = %s""", (env, contract_name))
            row = await cur.fetchone()
            if row is None:
                seed_raw = yaml.safe_load(SEED_PATH.read_text(encoding="utf-8")) or {}
                seed = _validated(seed_raw, contract_name, contract)
                cur = await conn.execute("""insert into mapping_state
                    (mapping, updated_by, pipeline_env, contract_name, event_type)
                    values (%s, 'seed', %s, %s, 'seed') returning id""",
                    (Json(seed.model_dump()), env, contract_name))
                version = (await cur.fetchone())["id"]
                await conn.execute("insert into mapping_current values (%s, %s, %s)", (env, contract_name, version))
                return seed, version
    return _validated(row["mapping"], contract_name, contract), row["id"]


async def get_mapping_by_version(version_id: int, pipeline_env: str | None = None, contract_name: str = "default",
                                 contract: Contract | None = None) -> MappingPatch:
    env = _env(pipeline_env)
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "select mapping from mapping_state where pipeline_env = %s and contract_name = %s and id = %s",
            (env, contract_name, version_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise ValueError(f"mapping version {version_id} not found")
    return _validated(row["mapping"], contract_name, contract)


async def save_mapping_cas(mapping: MappingPatch, expected_id: int, updated_by: str,
                           run_id: str | None = None, pipeline_env: str | None = None,
                           contract_name: str = "default", components: list[dict] | None = None,
                           contract_version: str | None = None, dataset_family: str | None = None,
                           decision: str | None = None, attempt_no: int | None = None,
                           contract: Contract | None = None) -> int:
    """Append one snapshot only while the version read by this run is current."""
    env = _env(pipeline_env)
    validated = _validated(mapping, contract_name, contract)
    async with get_pool().connection() as conn:
        async with conn.transaction():
            await conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (_scope(env, contract_name),))
            cur = await conn.execute(
                "select current_version_id as id from mapping_current where pipeline_env = %s and contract_name = %s for update",
                (env, contract_name),
            )
            row = await cur.fetchone()
            if row is None or row["id"] != expected_id:
                raise ValueError("mapping changed since this run read it")
            cur = await conn.execute(
                "insert into mapping_state (mapping, updated_by, pipeline_env, contract_name, run_id, parent_id, event_type) "
                "values (%s, %s, %s, %s, %s, %s, 'patch') returning id",
                (Json(validated.model_dump()), updated_by, env, contract_name, run_id, expected_id),
            )
            version = (await cur.fetchone())["id"]
            await conn.execute("update mapping_current set current_version_id = %s where pipeline_env = %s and contract_name = %s", (version, env, contract_name))
            if run_id:
                await conn.execute("update run_records set mapping_version_written = %s where run_id = %s", (version, run_id))
                for index, component in enumerate(components or []):
                    await conn.execute("""insert into component_events
                        (run_id, attempt_no, component_index, event_type, pipeline_env, contract_name,
                         contract_version, dataset_family, drift_class, specialist, confidence, patch,
                         mapping_version_id, decision)
                        values (%s, %s, %s, 'decision', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        on conflict (run_id, attempt_no, component_index, event_type)
                        do update set mapping_version_id = excluded.mapping_version_id""",
                        (run_id, attempt_no or 1, index, env, contract_name, contract_version,
                         dataset_family, component.get("drift_class"), component.get("specialist"),
                         component.get("confidence"), Json(component.get("patch") or {}), version, decision))
            return version


async def rollback_mapping_cas(failed_version_id: int, run_id: str, reason: str,
                               pipeline_env: str | None = None, contract_name: str = "default",
                               contract: Contract | None = None) -> int:
    env = _env(pipeline_env)
    if contract is None:
        _contract(contract_name)
    elif contract.name != contract_name:
        raise ValueError("contract scope and pinned snapshot disagree")
    async with get_pool().connection() as conn:
        async with conn.transaction():
            await conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (_scope(env, contract_name),))
            cur = await conn.execute("select current_version_id from mapping_current where pipeline_env = %s and contract_name = %s for update", (env, contract_name))
            current = await cur.fetchone()
            if current is None or current["current_version_id"] != failed_version_id:
                raise ValueError("rollback conflict: mapping changed after failed patch; manual reconciliation required")
            cur = await conn.execute("select parent_id from mapping_state where id = %s and pipeline_env = %s and contract_name = %s", (failed_version_id, env, contract_name))
            failed = await cur.fetchone()
            if failed is None or failed["parent_id"] is None:
                raise ValueError("rollback parent missing")
            parent_id = failed["parent_id"]
            cur = await conn.execute("select mapping from mapping_state where id = %s and pipeline_env = %s and contract_name = %s", (parent_id, env, contract_name))
            parent = await cur.fetchone()
            if parent is None:
                raise ValueError("rollback parent missing")
            cur = await conn.execute("""insert into mapping_state
                (mapping, updated_by, pipeline_env, contract_name, run_id, parent_id, event_type, restores_version_id, rollback_reason)
                values (%s, 'rollback', %s, %s, %s, %s, 'rollback', %s, %s) returning id""",
                (Json(parent["mapping"]), env, contract_name, run_id, failed_version_id, parent_id, reason))
            restored = (await cur.fetchone())["id"]
            await conn.execute("update mapping_current set current_version_id = %s where pipeline_env = %s and contract_name = %s", (restored, env, contract_name))
            return restored


async def save_mapping(
    mapping: MappingPatch,
    updated_by: str,
    pipeline_env: str | None = None,
    run_id: str | None = None,
    contract_name: str = "default",
    contract: Contract | None = None,
) -> MappingPatch:
    """Validates (again — cheap, and callers should never skip it) and
    appends a new row in this namespace. Returns the mapping actually stored.
    """
    env = _env(pipeline_env)
    validated = _validated(mapping, contract_name, contract)
    async with get_pool().connection() as conn:
        async with conn.transaction():
            await conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (_scope(env, contract_name),))
            cur = await conn.execute("select current_version_id from mapping_current where pipeline_env = %s and contract_name = %s for update", (env, contract_name))
            row = await cur.fetchone()
            parent_id = row["current_version_id"] if row else None
            cur = await conn.execute("""insert into mapping_state
                (mapping, updated_by, pipeline_env, contract_name, run_id, parent_id, event_type)
                values (%s, %s, %s, %s, %s, %s, 'manual') returning id""",
                (Json(validated.model_dump()), updated_by, env, contract_name, run_id, parent_id))
            version = (await cur.fetchone())["id"]
            await conn.execute("""insert into mapping_current values (%s, %s, %s)
                on conflict (pipeline_env, contract_name) do update set current_version_id = excluded.current_version_id""",
                (env, contract_name, version))
    return validated


async def mapping_history(limit: int = 20, pipeline_env: str | None = None, contract_name: str = "default") -> list[dict]:
    """Most recent snapshots first, in this namespace — every run mixed
    together. Useful for manual verification (confirming a save actually
    landed as a new row); the dashboard's per-run audit trail uses
    mapping_history_for_run instead, which doesn't mix runs.
    """
    env = _env(pipeline_env)
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "select id, mapping, updated_by, created_at, pipeline_env, contract_name, run_id, parent_id, event_type, restores_version_id, rollback_reason "
            "from mapping_state where pipeline_env = %s and contract_name = %s order by id desc limit %s",
            (env, contract_name, limit),
        )
        return list(await cur.fetchall())


async def mapping_history_for_run(
    run_id: str, pipeline_env: str | None = None, limit: int = 20, contract_name: str = "default"
) -> list[dict]:
    """The patches a single run actually made, oldest first — a small audit
    trail for the run detail view. The mapping_state table is append-only
    specifically so this falls out of a plain query rather than needing its
    own tracking: every patch a run's apply_patch node persisted is already
    sitting there, tagged with that run's id.
    """
    env = _env(pipeline_env)
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "select id, mapping, updated_by, created_at, parent_id, event_type, restores_version_id, rollback_reason "
            "from mapping_state where pipeline_env = %s and contract_name = %s and run_id = %s "
            "order by id asc limit %s",
            (env, contract_name, run_id, limit),
        )
        return list(await cur.fetchall())


# ── Run registry ─────────────────────────────────────────────────────────────
# The Postgres checkpointer (backend/services/checkpointer.py) is the source
# of truth for a run's actual state, keyed by thread_id — but it has no
# listing API: you must already know a thread_id to query it. This table is
# only an index (run_id, source_path, when) so the Phase 5 run list can
# enumerate what exists at all; GET /api/runs/{id} still asks the
# checkpointer for live status, not this table.


async def init_runs_table() -> None:
    async with get_pool().connection() as conn:
        await conn.execute(
            """
            create table if not exists run_records (
                run_id      text        primary key,
                source_path text        not null,
                pipeline_env text       not null,
                created_at  timestamptz not null default now()
            )
            """
        )
        await conn.execute(
            "create index if not exists idx_run_records_env_created "
            "on run_records (pipeline_env, created_at desc)"
        )
        await conn.execute("alter table run_records add column if not exists contract_name text not null default 'default'")
        await conn.execute("alter table run_records add column if not exists contract_version text")
        await conn.execute("alter table run_records add column if not exists source_ref jsonb")
        await conn.execute("alter table run_records add column if not exists mapping_scope text")
        await conn.execute("alter table run_records add column if not exists mapping_version_read bigint")
        await conn.execute("alter table run_records add column if not exists mapping_version_written bigint")
        await conn.execute("update run_records set mapping_scope = 'mapping:' || pipeline_env || ':' || contract_name where mapping_scope is null")
        await conn.execute("create index if not exists idx_run_records_scope_created on run_records (pipeline_env, contract_name, created_at desc)")
        await conn.execute("""create table if not exists source_snapshots (
            run_id text primary key, source_id text not null, payload bytea not null,
            sha256 text not null, row_count integer not null, profile_rows integer not null,
            captured_at timestamptz not null default now())""")


async def save_source_snapshot(run_id: str, source_id: str, payload: bytes, sha256: str,
                               row_count: int, profile_rows: int) -> None:
    async with get_pool().connection() as conn:
        await conn.execute("""insert into source_snapshots
            (run_id, source_id, payload, sha256, row_count, profile_rows)
            values (%s, %s, %s, %s, %s, %s) on conflict (run_id) do nothing""",
            (run_id, source_id, payload, sha256, row_count, profile_rows))


async def load_source_snapshot(run_id: str, expected_sha256: str) -> bytes:
    async with get_pool().connection() as conn:
        cur = await conn.execute("select payload, sha256 from source_snapshots where run_id = %s", (run_id,))
        row = await cur.fetchone()
    if row is None or row["sha256"] != expected_sha256:
        raise ValueError("source snapshot unavailable or changed")
    import hashlib
    payload = bytes(row["payload"])
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("source snapshot digest mismatch")
    return payload


async def record_run(run_id: str, source_path: str, pipeline_env: str | None = None,
                     contract_name: str = "default", contract_version: str | None = None,
                     source_ref: dict | None = None) -> None:
    env = _env(pipeline_env)
    contract = _contract(contract_name)
    async with get_pool().connection() as conn:
        await conn.execute(
            "insert into run_records (run_id, source_path, pipeline_env, contract_name, contract_version, source_ref, mapping_scope) values (%s, %s, %s, %s, %s, %s, %s) "
            "on conflict (run_id) do nothing",
            (run_id, source_path, env, contract_name, contract_version or contract.version,
             Json(source_ref) if source_ref else None, _scope(env, contract_name)),
        )


async def list_run_records(limit: int = 50, pipeline_env: str | None = None,
                           contract_name: str | None = "default") -> list[dict]:
    env = _env(pipeline_env)
    async with get_pool().connection() as conn:
        if contract_name is None:
            cur = await conn.execute("select run_id, source_path, source_ref, pipeline_env, contract_name, contract_version, created_at from run_records where pipeline_env = %s order by created_at desc limit %s", (env, limit))
        else:
            _contract(contract_name)
            cur = await conn.execute("select run_id, source_path, source_ref, pipeline_env, contract_name, contract_version, created_at from run_records where pipeline_env = %s and contract_name = %s order by created_at desc limit %s", (env, contract_name, limit))
        return list(await cur.fetchall())


async def get_run_record(run_id: str) -> dict | None:
    async with get_pool().connection() as conn:
        cur = await conn.execute("select run_id, source_path, pipeline_env, contract_name, contract_version, source_ref, mapping_scope, mapping_version_read, mapping_version_written, created_at from run_records where run_id = %s", (run_id,))
        return await cur.fetchone()


async def update_run_mapping_version(run_id: str, version_id: int, field: str) -> None:
    if field not in {"mapping_version_read", "mapping_version_written"}:
        raise ValueError("invalid mapping version field")
    async with get_pool().connection() as conn:
        await conn.execute(f"update run_records set {field} = %s where run_id = %s", (version_id, run_id))


async def init_component_tables() -> None:
    async with get_pool().connection() as conn:
        await conn.execute("""create table if not exists component_events (
            id bigserial primary key, run_id text not null, attempt_no integer not null,
            component_index integer not null, event_type text not null,
            pipeline_env text not null, contract_name text not null, contract_version text,
            dataset_family text, drift_class text, specialist text,
            confidence double precision, patch jsonb, mapping_version_id bigint,
            decision text, validation_passed boolean, detail text,
            created_at timestamptz not null default now(),
            unique (run_id, attempt_no, component_index, event_type))""")
        await conn.execute("""create table if not exists component_labels (
            id bigserial primary key, event_id bigint not null references component_events(id),
            correct boolean not null, labeled_by text not null, note text,
            created_at timestamptz not null default now())""")
        await conn.execute("create index if not exists idx_component_events_scope on component_events (pipeline_env, contract_name, event_type)")
        await conn.execute("""create table if not exists approval_decisions (
            run_id text not null, attempt_no integer not null, proposal_hash text not null,
            decision text not null, note text, pipeline_env text not null,
            contract_name text not null, contract_version text,
            finding_shape jsonb not null, final_status text,
            validation_passed boolean, claimed_at timestamptz not null default now(),
            lease_until timestamptz not null default now() + interval '60 seconds',
            primary key (run_id, attempt_no))""")


def proposal_hash(state: dict) -> str:
    import hashlib
    import json
    payload = json.dumps(state.get("proposed_patch") or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def finding_shape(state: dict) -> list[dict]:
    """No example values, row data, or free-form prompt text."""
    return [{"kind": str(f.get("kind", "unknown"))[:40],
             "check": str(f.get("check", "unknown"))[:80],
             "column": str(f.get("column", ""))[:80],
             "failure_count": int(f.get("failure_count", 0))}
            for f in (state.get("drift_items") or [])[:20]]


async def claim_approval(state: dict, decision: str, note: str | None) -> bool:
    """Return true only for a new or expired claim that should dispatch resume."""
    if decision not in {"approve", "reject"}:
        raise ValueError("invalid approval decision")
    run_id = state["run_id"]
    attempt = int(state.get("heal_attempts", 0)) + 1
    digest = proposal_hash(state)
    async with get_pool().connection() as conn:
        async with conn.transaction():
            cur = await conn.execute("""insert into approval_decisions
                (run_id, attempt_no, proposal_hash, decision, note, pipeline_env,
                 contract_name, contract_version, finding_shape)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (run_id, attempt_no) do nothing returning run_id""",
                (run_id, attempt, digest, decision, note, state.get("pipeline_env") or _env(None),
                 state.get("contract_name", "default"), state.get("contract_version"), Json(finding_shape(state))))
            if await cur.fetchone():
                return True
            cur = await conn.execute("""select proposal_hash, decision,
                lease_until < now() as lease_expired, final_status
                from approval_decisions where run_id=%s and attempt_no=%s for update""", (run_id, attempt))
            row = await cur.fetchone()
            if row["proposal_hash"] != digest or row["decision"] != decision:
                raise ValueError("conflicting approval decision for proposal")
            if row["final_status"] or not row["lease_expired"]:
                return False
            # An expired lease is eligible for retry only when no process is
            # still draining this graph. Session advisory locks disappear on
            # process crash, unlike the durable decision row.
            cur = await conn.execute("select pg_try_advisory_lock(hashtextextended(%s, 0)) as acquired", (run_id,))
            acquired = (await cur.fetchone())["acquired"]
            if not acquired:
                return False
            await conn.execute("select pg_advisory_unlock(hashtextextended(%s, 0))", (run_id,))
            await conn.execute("""update approval_decisions
                set lease_until = now() + interval '60 seconds'
                where run_id=%s and attempt_no=%s""", (run_id, attempt))
            return True


async def get_approval_decision(run_id: str) -> dict | None:
    async with get_pool().connection() as conn:
        cur = await conn.execute("""select * from approval_decisions where run_id=%s
            order by attempt_no desc limit 1""", (run_id,))
        return await cur.fetchone()


async def approval_history(run_id: str) -> list[dict]:
    async with get_pool().connection() as conn:
        cur = await conn.execute("""select run_id, attempt_no, proposal_hash, decision,
            note, pipeline_env, contract_name, contract_version, finding_shape,
            final_status, validation_passed, claimed_at
            from approval_decisions where run_id=%s order by attempt_no""", (run_id,))
        return list(await cur.fetchall())


async def labeled_approval_examples(pipeline_env: str, contract_name: str, specialist: str,
                                    exclude_run_id: str, limit: int = 200) -> list[dict]:
    """Retrieve reviewed positives only; never return patches or sample values."""
    async with get_pool().connection() as conn:
        cur = await conn.execute("""select e.id, e.run_id, e.specialist,
            a.finding_shape, a.final_status
            from component_events e
            join approval_decisions a on a.run_id=e.run_id and a.attempt_no=e.attempt_no
            join lateral (select correct from component_labels l where l.event_id=e.id
                          order by l.id desc limit 1) label on true
            where e.event_type='proposal' and e.pipeline_env=%s
              and e.contract_name=%s and e.specialist=%s
              and e.run_id<>%s and a.decision='approve' and a.validation_passed=true
              and label.correct=true
            order by e.id desc limit %s""",
            (pipeline_env, contract_name, specialist, exclude_run_id, limit))
        return list(await cur.fetchall())


async def acquire_run_execution_lock(conn, run_id: str) -> bool:
    # Blocking acquisition serializes a resume queued while the initial
    # execution is still unwinding from interrupt(). A try-lock would drop
    # that sole durable resume dispatch.
    await conn.execute("select pg_advisory_lock(hashtextextended(%s, 0))", (run_id,))
    return True


async def release_run_execution_lock(conn, run_id: str) -> None:
    await conn.execute("select pg_advisory_unlock(hashtextextended(%s, 0))", (run_id,))


async def record_approval_outcome(run_id: str, status: str, validation_passed: bool | None) -> None:
    if status not in {"clean", "healed", "alert", "escalated"}:
        return
    async with get_pool().connection() as conn:
        await conn.execute("""update approval_decisions set final_status=%s,
            validation_passed=%s where run_id=%s and final_status is null""",
            (status, validation_passed, run_id))


async def record_component_event(state: dict, component_index: int, event_type: str,
                                 decision: str | None = None, validation_passed: bool | None = None,
                                 mapping_version_id: int | None = None, detail: str | None = None,
                                 attempt_no: int | None = None) -> None:
    component = (state.get("components") or [{}])[component_index]
    dataset_family = Path(state.get("source_path") or "").stem.split("_")[0]
    async with get_pool().connection() as conn:
        await conn.execute("""insert into component_events
            (run_id, attempt_no, component_index, event_type, pipeline_env, contract_name,
             contract_version, dataset_family, drift_class, specialist, confidence, patch,
             mapping_version_id, decision, validation_passed, detail)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (run_id, attempt_no, component_index, event_type) do nothing""",
            (state["run_id"], attempt_no if attempt_no is not None else state.get("heal_attempts", 0) + (event_type != "outcome"),
             component_index, event_type, state.get("pipeline_env") or _env(None),
             state.get("contract_name", "default"), state.get("contract_version"),
             dataset_family, component.get("drift_class"), component.get("specialist"),
             component.get("confidence"), Json(component.get("patch") or {}),
             mapping_version_id, decision, validation_passed, detail))


async def component_event_rows(pipeline_env: str | None = None,
                               contract_name: str = "default") -> list[dict]:
    env = _env(pipeline_env)
    _contract(contract_name)
    async with get_pool().connection() as conn:
        cur = await conn.execute("""select e.*, l.correct as labeled_correct from component_events e
            left join lateral (select correct from component_labels where event_id = e.id order by id desc limit 1) l on true
            where e.pipeline_env = %s and e.contract_name = %s order by e.id""", (env, contract_name))
        return list(await cur.fetchall())


async def label_component(event_id: int, correct: bool, labeled_by: str,
                          note: str | None = None) -> None:
    async with get_pool().connection() as conn:
        cur = await conn.execute("select id from component_events where id = %s and event_type = 'proposal'", (event_id,))
        if await cur.fetchone() is None:
            raise ValueError("proposal event not found")
        await conn.execute("insert into component_labels (event_id, correct, labeled_by, note) values (%s, %s, %s, %s)",
                           (event_id, correct, labeled_by, note))


async def component_event_scope(event_id: int) -> dict | None:
    async with get_pool().connection() as conn:
        cur = await conn.execute("""select e.pipeline_env, r.source_ref
            from component_events e left join run_records r on r.run_id = e.run_id
            where e.id = %s and e.event_type = 'proposal'""", (event_id,))
        return await cur.fetchone()
