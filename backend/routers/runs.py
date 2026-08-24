"""Trigger, poll, approve/reject a healing run — the HTTP surface Phase 4
needs to prove interrupt + Postgres checkpointer + resume-after-restart
actually works over real HTTP requests, not just inside a script.

Run the graph in a FastAPI BackgroundTask; poll rather than WebSocket
(ARCHITECTURE.md's API section, "Run the graph in a FastAPI BackgroundTask").
Upload and run-listing endpoints are Phase 5 dashboard concerns, not needed
to prove this phase's mechanism — deliberately not built here.
"""

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from langgraph.types import Command
from pydantic import BaseModel

from auth import require_token
from graph.state import RECURSION_LIMIT

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"], dependencies=[Depends(require_token)])


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


def _serialize_state(run_id: str, snapshot) -> dict:
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
        **values,
    }


@router.post("")
async def trigger_run(
    body: TriggerRunRequest,
    background: BackgroundTasks,
    graph_app=Depends(_graph_app),
) -> dict:
    run_id = str(uuid.uuid4())
    background.add_task(_execute, graph_app, run_id, _blank_state(run_id, body.source_path))
    return {"run_id": run_id}


@router.get("/{run_id}")
async def get_run(run_id: str, graph_app=Depends(_graph_app)) -> dict:
    snapshot = await graph_app.aget_state({"configurable": {"thread_id": run_id}})
    if not snapshot.values:
        raise HTTPException(status_code=404, detail="run not found")
    return _serialize_state(run_id, snapshot)


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
