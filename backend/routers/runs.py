"""Trigger, poll, approve/reject a healing run — the HTTP surface Phase 4
needs to prove interrupt + Postgres checkpointer + resume-after-restart
actually works over real HTTP requests, not just inside a script.

Run the graph in a FastAPI BackgroundTask; poll rather than WebSocket
(ARCHITECTURE.md's API section, "Run the graph in a FastAPI BackgroundTask").
Upload and run-listing endpoints are Phase 5 dashboard concerns, not needed
to prove this phase's mechanism — deliberately not built here.

Found on the deployed backend, not locally: main.py's lifespan calls
checkpointer.setup_checkpointer() once at startup, wrapped in the same
best-effort try/except as db.init_schema() — a cold Neon compute can make
that first call fail, exactly the class of failure db.py already retries on
first /api/ping. Every route here previously had no equivalent, so
GET /api/runs/{id} 500'd until something happened to call /api/ping first.
_ensure_checkpointer_ready, below, closes that gap directly on this
router — idempotent and cheap once the tables exist, so retrying it on
every request costs one metadata SELECT, not a real setup re-run.
"""

import logging
import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from langgraph.types import Command
from pydantic import BaseModel
import psycopg

from auth import require_token
from graph.state import RECURSION_LIMIT
from services import checkpointer, store
from services.analytics import collect_run_summaries, invalidate_run_summaries
from pipeline.contract import get_registry
from pipeline.distribution import load_baseline
from pipeline.temporal import load_temporal_rule
from pipeline.sources import SourceUnavailable, materialize_source
from config import get_settings

log = logging.getLogger(__name__)

# The client (the dashboard's dataset picker) only knows the fault datasets
# by name, not by any path meaningful on the server's filesystem — resolving
# a bare filename here, server-side, is simpler than pushing filesystem
# knowledge into the frontend or building a real upload flow neither Phase 5
# nor the demo needs.
DATA_SOURCES_DIR = Path(__file__).resolve().parents[2] / "data" / "sources"
_schema_ready = False
_schema_lock = asyncio.Lock()


def _resolve_source_path(source_path: str) -> str:
    candidate = Path(source_path)
    if candidate.name != source_path or candidate.suffix.lower() != ".csv":
        raise HTTPException(status_code=422, detail="source_path must be an available CSV source ID")
    resolved = (DATA_SOURCES_DIR / source_path).resolve()
    if resolved.parent != DATA_SOURCES_DIR.resolve() or not resolved.is_file():
        raise HTTPException(status_code=422, detail="unknown CSV source ID")
    return str(resolved)


async def _ensure_checkpointer_ready() -> None:
    global _schema_ready
    try:
        await checkpointer.setup_checkpointer()
        if not _schema_ready:
            async with _schema_lock:
                if not _schema_ready:
                    await store.init_mapping_table()
                    await store.init_runs_table()
                    await store.init_component_tables()
                    _schema_ready = True
    except Exception:
        log.exception("runs: startup retry failed")
        raise HTTPException(status_code=503, detail="checkpoint store unavailable, try again")


router = APIRouter(
    prefix="/api/runs",
    tags=["runs"],
    dependencies=[Depends(require_token), Depends(_ensure_checkpointer_ready)],
)


class TriggerRunRequest(BaseModel):
    source_id: str | None = None
    source_path: str | None = None  # accepted for older dashboard clients; still an ID
    contract_name: str = "default"


class ApprovalRequest(BaseModel):
    note: str | None = None


def _blank_state(run_id: str, source_path: str, contract_name: str = "default",
                 contract_version: str | None = None, contract_snapshot: dict | None = None,
                 pipeline_env: str | None = None, source_family: str | None = None,
                 source_ref: dict | None = None, distribution_baseline: dict | None = None,
                 temporal_rule: dict | None = None, run_clock_utc: str | None = None) -> dict:
    return {
        "run_id": run_id,
        "source_path": source_path,
        "source_family": source_family,
        "source_ref": source_ref,
        "distribution_baseline": distribution_baseline,
        "distribution_alerts": [],
        "distribution_skipped": [],
        "distribution_profile": {},
        "distribution_baseline_id": None,
        "distribution_baseline_version": None,
        "distribution_baseline_digest": None,
        "monitor_error": None,
        "monitor_completed": False,
        "temporal_rule": temporal_rule,
        "run_clock_utc": run_clock_utc,
        "temporal_alerts": [],
        "temporal_skipped": [],
        "temporal_profile": {},
        "temporal_rule_digest": None,
        "temporal_monitor_error": None,
        "temporal_monitor_completed": False,
        "contract_name": contract_name,
        "contract_version": contract_version,
        "contract_snapshot": contract_snapshot,
        "pipeline_env": pipeline_env,
        "mapping_scope": f"mapping:{pipeline_env}:{contract_name}" if pipeline_env else None,
        "baseline_failures": [],
        "rollback_version": None,
        "rollback_reason": None,
        "audit_error": None,
        "assertions_passed": False,
        "assertion_failures": [],
        "rows_in": 0,
        "rows_out": 0,
        "actual_schema": {},
        "expected_schema": {},
        "drift_items": [],
        "drift_class": None,
        "diagnosis": None,
        "confidence": None,
        "proposed_patch": None,
        "patch_rationale": None,
        "applied_patch": None,
        "awaiting_approval": False,
        "human_decision": None,
        "human_note": None,
        "heal_attempts": 0,
        "status": "running",
        "final_message": None,
        "history": [],
        "assertion_failure_details": [],
        "source_profile": {},
        "specialist_output": None,
        "specialist_outputs": None,
        "candidates": [],
        "components": [],
        "mapping_version_read": None,
        "mapping_version_written": None,
        "source_sha256": None,
        "patch_error": None,
        "proposal_error": None,
    }


def _graph_app(request: Request):
    return request.app.state.graph_app


async def _execute(graph_app, run_id: str, stream_input,
                   pipeline_env: str | None = None, contract_name: str = "default") -> None:
    """Drains the graph's stream to completion — a pause at an interrupt and
    a terminal node both end the stream the same way. Runs detached from the
    triggering request; the client polls GET /api/runs/{run_id}.

    Every node name is logged as it fires. This is what makes
    resume-after-restart externally verifiable without trusting internal
    state alone: a genuine resume shows "human_approval" (and only nodes
    after it) firing in THIS process's log, with no run_pipeline/diagnose
    entries — a checkpointer that silently restarted from START would show
    the full sequence again, and the log would catch it.
    """
    cfg = {"configurable": {"thread_id": run_id}, "recursion_limit": RECURSION_LIMIT}
    try:
        # A dedicated connection holds the session lock without consuming a
        # slot from the application's eight-connection worker pool.
        async with await psycopg.AsyncConnection.connect(
            get_settings().database_url, connect_timeout=10, autocommit=True
        ) as lock_conn:
            await lock_conn.execute("select set_config('statement_timeout', '120000', false)")
            if not await store.acquire_run_execution_lock(lock_conn, run_id):
                log.warning("run %s: duplicate execution skipped", run_id)
                return
            try:
                async for event in graph_app.astream(stream_input, config=cfg, stream_mode="updates"):
                    for node_name in event:
                        log.info("run %s: node %r fired", run_id, node_name)
            finally:
                await store.release_run_execution_lock(lock_conn, run_id)
    except Exception:
        log.exception("run %s: background execution failed", run_id)
    finally:
        try:
            snapshot = await graph_app.aget_state({"configurable": {"thread_id": run_id}})
            values = snapshot.values or {}
            validated = (bool(values.get("assertions_passed"))
                         if values.get("human_decision") == "approve" and values.get("heal_attempts", 0) > 0
                         else None)
            await store.record_approval_outcome(run_id, values.get("status", "running"),
                                                validated)
        except Exception:
            log.exception("run %s: approval outcome audit failed", run_id)
        invalidate_run_summaries(pipeline_env, contract_name)


def _serialize_state(run_id: str, snapshot, mapping_audit: list[dict] | None = None) -> dict:
    values = dict(snapshot.values) if snapshot.values else {}
    source_ref = values.get("source_ref") or {}

    interrupts = [i.value for task in snapshot.tasks for i in task.interrupts]
    awaiting = bool(interrupts)
    # HealState's own "status" field can only be set by a node that actually
    # returns — human_approval never returns while paused inside interrupt(),
    # so there is no way for it to have written "awaiting_approval" into
    # state. The checkpointer's own pause signal (snapshot.next / pending
    # interrupts) is the authoritative source for that instead.
    api_status = "awaiting_approval" if awaiting else values.get("status", "running")

    # `**values` MUST come first: HealState carries its own `awaiting_approval`
    # field (set False in _blank_state, never updated by any node while
    # actually paused — human_approval never returns until resumed), which
    # collides by name with the synthesized key below. Spreading `values`
    # last silently let that stale False win over the live, correct one —
    # found 2026-08-25 via a seed script whose polling loop, which reads this
    # exact field, could never detect a real interrupt. Since ApprovalPanel's
    # entire visibility check is `detail?.awaiting_approval`, this also meant
    # the human-approval UI panel had never actually appeared for a real
    # interrupted run, in this project's history, until this line changed.
    return {
        **values,
        "source_id": source_ref.get("source_id") or Path(values.get("source_path") or "").name,
        "source_path": (source_ref["source_id"] if source_ref.get("kind") in {"database_table", "local_json"}
                        else values.get("source_path")),
        "run_id": run_id,
        "api_status": api_status,
        "awaiting_approval": awaiting,
        "interrupt": interrupts[0] if interrupts else None,
        "next": list(snapshot.next),
        "mapping_audit": mapping_audit or [],
    }


@router.post("")
async def trigger_run(
    body: TriggerRunRequest,
    background: BackgroundTasks,
    graph_app=Depends(_graph_app),
) -> dict:
    run_id = str(uuid.uuid4())
    try:
        contract = get_registry().get(body.contract_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    env = get_settings().pipeline_env
    source_id = body.source_id or body.source_path
    if not source_id or (body.source_id and body.source_path and body.source_id != body.source_path):
        raise HTTPException(status_code=422, detail="one source ID is required")
    try:
        source = await materialize_source(source_id, contract.name, run_id)
        baseline = load_baseline(contract.name, source.family)
        temporal_rule = load_temporal_rule(contract.name, source.family)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SourceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await store.record_run(run_id, source.path, env, contract.name, contract.version, source.ref)
    invalidate_run_summaries(env, contract.name)
    background.add_task(_execute, graph_app, run_id,
                        _blank_state(run_id, source.path, contract.name, contract.version, contract.spec, env,
                                     source.family, source.ref, baseline, temporal_rule,
                                     datetime.now(timezone.utc).isoformat()),
                        env, contract.name)
    return {"run_id": run_id}


@router.get("")
async def list_runs(
    graph_app=Depends(_graph_app),
    limit: int = Query(50, ge=1, le=500),
    status: str | None = Query(None, description="filter: clean/healed/escalated/awaiting_approval/running"),
    dataset: str | None = Query(None, description="filter: dataset filename, e.g. day4_combo.csv"),
    contract_name: str = Query("default"),
) -> dict:
    """Enumerates run_records (the lightweight index, not the checkpointer)
    via collect_run_summaries — the same checkpointer-backed aggregation the
    /api/analytics/* endpoints use (see services/analytics.py) — and returns
    a lean per-run projection rather than full HealState. Fine at demo scale
    (dozens of runs, not thousands) — always-fresh beats a second place for
    status to go stale, and there is no listing API on the checkpointer
    itself to fall back to.

    Filters and `limit` apply after aggregation, not in SQL: status and
    dataset are both derived fields (status can reflect a live interrupt;
    dataset is the source_path's basename), not raw columns to filter on.
    """
    try:
        get_registry().get(contract_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    runs = await collect_run_summaries(graph_app, contract_name=contract_name)
    if status:
        runs = [r for r in runs if r["status"] == status]
    if dataset:
        runs = [r for r in runs if r["dataset"] == dataset]
    runs.sort(key=lambda r: r["created_at"], reverse=True)
    runs = runs[:limit]

    return {
        "runs": [
            {
                "run_id": r["run_id"],
                "dataset": r["dataset"],
                "source_path": r["source_path"],
                "source_id": r["dataset"],
                "status": r["status"],
                "api_status": r["status"],  # kept alongside `status` for any existing caller
                "drift_class": r["drift_class"],
                "heal_attempts": r["heal_attempts"],
                "final_confidence": r["final_confidence"],
                "approved_by": r["approved_by"],
                "duration_ms": r["duration_ms"],
                "created_at": r["created_at"],
                "contract_name": r["contract_name"],
                "contract_version": r["contract_version"],
            }
            for r in runs
        ]
    }


@router.get("/{run_id}")
async def get_run(run_id: str, graph_app=Depends(_graph_app)) -> dict:
    snapshot = await graph_app.aget_state({"configurable": {"thread_id": run_id}})
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="run not found")
    record = await store.get_run_record(run_id)
    # Checkpoints may predate run_records. New runs use the stored scope;
    # historical checkpoint-only runs retain their default-scope audit.
    values = snapshot.values
    env = record["pipeline_env"] if record else values.get("pipeline_env")
    contract_name = record["contract_name"] if record else values.get("contract_name", "default")
    audit = await store.mapping_history_for_run(run_id, env, contract_name=contract_name)
    detail = _serialize_state(run_id, snapshot, mapping_audit=audit)
    detail["approval_history"] = await store.approval_history(run_id)
    return detail


@router.get("/{run_id}/approvals")
async def get_run_approvals(run_id: str) -> dict:
    if await store.get_run_record(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {"approvals": await store.approval_history(run_id)}


async def _resume(
    run_id: str,
    decision: str,
    note: str | None,
    background: BackgroundTasks,
    graph_app,
) -> dict:
    cfg = {"configurable": {"thread_id": run_id}}
    snapshot = await graph_app.aget_state(cfg)
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="run not found")
    if snapshot.next != ("human_approval",):
        existing = await store.get_approval_decision(run_id)
        if existing and existing["decision"] == decision:
            return {"run_id": run_id, "resumed_with": decision, "already_recorded": True}
        raise HTTPException(
            status_code=409,
            detail=f"run is not awaiting approval (next={list(snapshot.next)})",
        )

    state = dict(snapshot.values)
    try:
        should_dispatch = await store.claim_approval(state, decision, note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not should_dispatch:
        return {"run_id": run_id, "resumed_with": decision, "already_recorded": True}
    try:
        for index in range(len(state.get("components") or [{}])):
            await store.record_component_event(state, index, "decision",
                decision="human_approved" if decision == "approve" else "human_rejected", detail=note)
            if decision == "reject":
                await store.record_component_event(state, index, "outcome",
                    detail="human rejected before mapping write", attempt_no=state.get("heal_attempts", 0) + 1)
    except Exception:
        log.exception("run %s: component decision audit failed", run_id)

    background.add_task(
        _execute, graph_app, run_id, Command(resume={"decision": decision, "note": note}),
        snapshot.values.get("pipeline_env"), snapshot.values.get("contract_name", "default")
    )
    invalidate_run_summaries(snapshot.values.get("pipeline_env"), snapshot.values.get("contract_name", "default"))
    return {"run_id": run_id, "resumed_with": decision}


@router.post("/{run_id}/approve")
async def approve_run(
    run_id: str,
    background: BackgroundTasks,
    body: ApprovalRequest | None = None,
    graph_app=Depends(_graph_app),
) -> dict:
    note = body.note if body else None
    return await _resume(run_id, "approve", note, background, graph_app)


@router.post("/{run_id}/reject")
async def reject_run(
    run_id: str,
    background: BackgroundTasks,
    body: ApprovalRequest | None = None,
    graph_app=Depends(_graph_app),
) -> dict:
    note = body.note if body else None
    return await _resume(run_id, "reject", note, background, graph_app)
