"""Authenticated bridge from immutable staged orders to an agent run."""

from datetime import datetime, timezone
from pathlib import Path
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from auth import require_loader, require_approver
from config import get_settings
from integrations.orders import feed_scope, feed_spec, validate_batch_id
from pipeline.contract import get_registry, contract_for_state
from pipeline.sources import SNAPSHOT_DIR
from routers.runs import ApprovalRequest, _blank_state, _ensure_checkpointer_ready, _execute, _resume, _serialize_state
from services import external_store, store

router = APIRouter(prefix="/api/external/orders", tags=["external orders"],
                   dependencies=[Depends(_ensure_checkpointer_ready)])


def _ids(feed_id: str, batch_id: str) -> tuple[str, str]:
    try:
        feed_spec(feed_id)
        validate_batch_id(batch_id)
        return feed_id, batch_id
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{feed_id}/{batch_id}/trigger", dependencies=[Depends(require_loader)])
async def trigger_external_batch(feed_id: str, batch_id: str, background: BackgroundTasks,
                                 request: Request) -> dict:
    _ids(feed_id, batch_id)
    if get_settings().auth_mode != "production":
        await external_store.init_external_tables()
    contract = get_registry().get(feed_spec(feed_id)["contract_name"])
    env = feed_scope(get_settings().pipeline_env, feed_id)
    proposed = str(uuid.uuid4())
    proposed_path = str(SNAPSHOT_DIR / f"{proposed}.csv")
    try:
        claim = await external_store.prepare_run(feed_id, batch_id, proposed, env,
                                                 contract.name, contract.version, proposed_path)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    run_id = claim["run_id"]
    path = SNAPSHOT_DIR / f"{run_id}.csv"
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    path.write_bytes(claim["payload"])
    graph = request.app.state.graph_app
    snapshot = await graph.aget_state({"configurable": {"thread_id": run_id}})
    if not snapshot.values and await external_store.claim_dispatch(feed_id, batch_id, run_id):
        ref = {"kind": "external_batch", "source_id": f"{feed_id}/{batch_id}",
               "feed_id": feed_id, "batch_id": batch_id, "sha256": claim["sha256"],
               "row_count": claim["row_count"]}
        state = _blank_state(run_id, str(path), contract.name, contract.version,
                             contract.spec, env, f"external:{feed_id}", ref,
                             None, None, datetime.now(timezone.utc).isoformat())
        background.add_task(_execute, graph, run_id, state, env, contract.name)
    return {"run_id": run_id, "feed_id": feed_id, "batch_id": batch_id,
            "sha256": claim["sha256"], "already_registered": not claim["created"]}


@router.post("/{feed_id}/{batch_id}/release", dependencies=[Depends(require_loader)])
async def release_external_batch(feed_id: str, batch_id: str, request: Request) -> dict:
    _ids(feed_id, batch_id)
    row = await external_store.get_batch(feed_id, batch_id)
    if not row or not row["agent_run_id"]:
        raise HTTPException(status_code=404, detail="batch has no agent run")
    run_id = row["agent_run_id"]
    snapshot = await request.app.state.graph_app.aget_state({"configurable": {"thread_id": run_id}})
    state = snapshot.values or {}
    record = await store.get_run_record(run_id)
    env = feed_scope(get_settings().pipeline_env, feed_id)
    ref = (record or {}).get("source_ref") or {}
    if (not record or record["pipeline_env"] != env or
            record["contract_name"] != "default" or
            ref.get("kind") != "external_batch" or
            ref.get("feed_id") != feed_id or ref.get("batch_id") != batch_id or
            ref.get("sha256") != row["sha256"] or
            state.get("source_sha256") != row["sha256"]):
        raise HTTPException(status_code=409, detail="agent run does not match staged batch")
    if (snapshot.next or state.get("status") not in {"clean", "healed"} or
            not state.get("assertions_passed") or state.get("patch_error") or
            state.get("monitor_error") or state.get("temporal_monitor_error")):
        raise HTTPException(status_code=409, detail="batch is not validated for publish")
    version = state.get("mapping_version_written") or state.get("mapping_version_read")
    if not isinstance(version, int) or not await external_store.version_survived_rollback(version, env, "default"):
        raise HTTPException(status_code=409, detail="mapping version was rolled back or unavailable")
    contract = contract_for_state(state)
    mapping = await store.get_mapping_by_version(version, env, "default", contract)
    manifest = {"feed_id": feed_id, "batch_id": batch_id, "sha256": row["sha256"],
            "run_id": run_id, "mapping_version": version, "mapping": mapping.model_dump(),
            "pipeline_env": env, "contract_version": contract.version,
            "row_count": row["row_count"]}
    try:
        await external_store.authorize_release(feed_id, batch_id, manifest)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return manifest


async def _batch_run(feed_id: str, batch_id: str) -> str:
    _ids(feed_id, batch_id)
    row = await external_store.get_batch(feed_id, batch_id)
    if not row or not row["agent_run_id"]:
        raise HTTPException(status_code=404, detail="batch has no agent run")
    record = await store.get_run_record(row["agent_run_id"])
    ref = (record or {}).get("source_ref") or {}
    if (not record or record["pipeline_env"] != feed_scope(get_settings().pipeline_env, feed_id)
            or ref.get("kind") != "external_batch" or ref.get("feed_id") != feed_id
            or ref.get("batch_id") != batch_id or ref.get("sha256") != row["sha256"]):
        raise HTTPException(status_code=409, detail="batch run scope mismatch")
    return row["agent_run_id"]


@router.get("/{feed_id}/{batch_id}/status", dependencies=[Depends(require_loader)])
async def external_batch_status(feed_id: str, batch_id: str, request: Request) -> dict:
    run_id = await _batch_run(feed_id, batch_id)
    snapshot = await request.app.state.graph_app.aget_state({"configurable": {"thread_id": run_id}})
    if not snapshot.values:
        return {"run_id": run_id, "api_status": "running"}
    detail = _serialize_state(run_id, snapshot)
    return {"run_id": run_id, "api_status": detail["api_status"]}


@router.post("/{feed_id}/{batch_id}/approve", dependencies=[Depends(require_approver)])
async def approve_external_batch(feed_id: str, batch_id: str, background: BackgroundTasks,
                                 request: Request, body: ApprovalRequest | None = None) -> dict:
    return await _resume(await _batch_run(feed_id, batch_id), "approve", body.note if body else None, background,
                         request.app.state.graph_app)


@router.post("/{feed_id}/{batch_id}/reject", dependencies=[Depends(require_approver)])
async def reject_external_batch(feed_id: str, batch_id: str, background: BackgroundTasks,
                                request: Request, body: ApprovalRequest | None = None) -> dict:
    return await _resume(await _batch_run(feed_id, batch_id), "reject", body.note if body else None, background,
                         request.app.state.graph_app)
