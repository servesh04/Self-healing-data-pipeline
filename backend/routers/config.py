"""GET /api/config/mapping — the pipeline's current live mapping
(ARCHITECTURE.md's API section: "show it changing live"). Independent of any
specific run: this is the one global mapping every future run reads.

GET /api/config/mapping/history (DASHBOARD.md's "Mapping" page) is a
straight read of the same append-only mapping_state table — no new SQL, no
new table, just store.mapping_history() (already written in Phase 1)
exposed over HTTP for the first time.
"""

from fastapi import APIRouter, Depends, Query, HTTPException

from auth import require_token
from services import store
from pipeline.contract import get_registry

router = APIRouter(prefix="/api/config", tags=["config"], dependencies=[Depends(require_token)])


@router.get("/mapping")
async def get_mapping(contract_name: str = Query("default")) -> dict:
    try:
        get_registry().get(contract_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    mapping = await store.get_current_mapping(contract_name=contract_name)
    return mapping.model_dump()


@router.get("/mapping/history")
async def get_mapping_history(limit: int = Query(50, ge=1, le=500), contract_name: str = Query("default")) -> dict:
    # Most-recent-first from the store; the timeline UI wants oldest-first
    # so newer entries read top-to-bottom the way they actually happened.
    try:
        get_registry().get(contract_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    rows = await store.mapping_history(limit=limit, contract_name=contract_name)
    return {"history": list(reversed(rows))}


@router.get("/contracts")
async def list_contracts() -> dict:
    registry = get_registry()
    return {"contracts": [{"name": name, "version": registry.get(name).version,
                            "columns": list(registry.get(name).expected_dtypes)} for name in registry.names()]}
