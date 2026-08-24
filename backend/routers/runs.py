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
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from langgraph.types import Command
from pydantic import BaseModel

from auth import require_token
from graph.state import RECURSION_LIMIT
from services import checkpointer, store

log = logging.getLogger(__name__)

# The client (the dashboard's dataset picker) only knows the fault datasets
# by name, not by any path meaningful on the server's filesystem — resolving
# a bare filename here, server-side, is simpler than pushing filesystem
# knowledge into the frontend or building a real upload flow neither Phase 5
# nor the demo needs.
DATA_SOURCES_DIR = Path(__file__).resolve().parents[2] / "data" / "sources"


def _resolve_source_path(source_path: str) -> str:
    candidate = Path(source_path)
    if candidate.is_absolute() or candidate.exists():
        return str(candidate)
    return str(DATA_SOURCES_DIR / candidate.name)


async def _ensure_checkpointer_ready() -> None:
    try:
        await checkpointer.setup_checkpointer()
        await store.init_runs_table()  # same cold-start class; same fix
    except Exception:
        log.exception("runs: startup retry failed")
        raise HTTPException(status_code=503, detail="checkpoint store unavailable, try again")


router = APIRouter(
    prefix="/api/runs",
    tags=["runs"],
    dependencies=[Depends(require_token), Depends(_ensure_checkpointer_ready)],
)


class TriggerRunRequest(BaseModel):
    source_path: str


class ApprovalRequest(BaseModel):
    note: str | None = None


def _blank_state(run_id: str, source_path: str) -> dict:
    return {
        "run_id": run_id,
        "source_path": source_path,
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
    }


def _graph_app(request: Request):
    return request.app.state.graph_app


async def _execute(graph_app, run_id: str, stream_input) -> None:
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
        async for event in graph_app.astream(stream_input, config=cfg, stream_mode="updates"):
            for node_name in event:
                log.info("run %s: node %r fired", run_id, node_name)
    except Exception:
        log.exception("run %s: background execution failed", run_id)


def _serialize_state(run_id: str, snapshot, mapping_audit: list[dict] | None = None) -> dict:
    values = dict(snapshot.values) if snapshot.values else {}

    interrupts = [i.value for task in snapshot.tasks for i in task.interrupts]
    awaiting = bool(interrupts)
    # HealState's own "status" field can only be set by a node that actually
    # returns — human_approval never returns while paused inside interrupt(),
    # so there is no way for it to have written "awaiting_approval" into
    # state. The checkpointer's own pause signal (snapshot.next / pending
    # interrupts) is the authoritative source for that instead.
    api_status = "awaiting_approval" if awaiting else values.get("status", "running")

    return {
        "run_id": run_id,
        "api_status": api_status,
        "awaiting_approval": awaiting,
        "interrupt": interrupts[0] if interrupts else None,
        "next": list(snapshot.next),
        "mapping_audit": mapping_audit or [],
        **values,
    }


@router.post("")
async def trigger_run(
    body: TriggerRunRequest,
    background: BackgroundTasks,
    graph_app=Depends(_graph_app),
) -> dict:
    run_id = str(uuid.uuid4())
    source_path = _resolve_source_path(body.source_path)
    await store.record_run(run_id, source_path)
    background.add_task(_execute, graph_app, run_id, _blank_state(run_id, source_path))
    return {"run_id": run_id}


@router.get("")
async def list_runs(graph_app=Depends(_graph_app)) -> dict:
    """Enumerates run_records (the lightweight index, not the checkpointer)
    and asks the checkpointer for each one's live status. Fine at demo scale
    (dozens of runs, not thousands) — always-fresh beats a second place for
    status to go stale, and there is no listing API on the checkpointer
    itself to fall back to.
    """
    records = await store.list_run_records()
    runs = []
    for record in records:
        run_id = record["run_id"]
        snapshot = await graph_app.aget_state({"configurable": {"thread_id": run_id}})
        summary = _serialize_state(run_id, snapshot) if snapshot.values else {}
        runs.append(
            {
                "run_id": run_id,
                "source_path": record["source_path"],
                "created_at": record["created_at"],
                "api_status": summary.get("api_status", "unknown"),
                "heal_attempts": summary.get("heal_attempts", 0),
            }
        )
    return {"runs": runs}


@router.get("/{run_id}")
async def get_run(run_id: str, graph_app=Depends(_graph_app)) -> dict:
    snapshot = await graph_app.aget_state({"configurable": {"thread_id": run_id}})
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="run not found")
    audit = await store.mapping_history_for_run(run_id)
    return _serialize_state(run_id, snapshot, mapping_audit=audit)


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
        raise HTTPException(
            status_code=409,
            detail=f"run is not awaiting approval (next={list(snapshot.next)})",
        )

    background.add_task(
        _execute, graph_app, run_id, Command(resume={"decision": decision, "note": note})
    )
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
