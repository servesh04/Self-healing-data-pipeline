"""GET /api/config/mapping — the pipeline's current live mapping
(ARCHITECTURE.md's API section: "show it changing live"). Independent of any
specific run: this is the one global mapping every future run reads.

GET /api/config/mapping/history (DASHBOARD.md's "Mapping" page) is a
straight read of the same append-only mapping_state table — no new SQL, no
new table, just store.mapping_history() (already written in Phase 1)
exposed over HTTP for the first time.
"""

from fastapi import APIRouter, Depends, Query

from auth import require_token
from services import store

router = APIRouter(prefix="/api/config", tags=["config"], dependencies=[Depends(require_token)])


@router.get("/mapping")
async def get_mapping() -> dict:
    mapping = await store.get_current_mapping()
    return mapping.model_dump()


@router.get("/mapping/history")
async def get_mapping_history(limit: int = Query(50, ge=1, le=500)) -> dict:
    # Most-recent-first from the store; the timeline UI wants oldest-first
    # so newer entries read top-to-bottom the way they actually happened.
    rows = await store.mapping_history(limit=limit)
    return {"history": list(reversed(rows))}
