"""GET /api/config/mapping — the pipeline's current live mapping
(ARCHITECTURE.md's API section: "show it changing live"). Independent of any
specific run: this is the one global mapping every future run reads.
"""

from fastapi import APIRouter, Depends

from auth import require_token
from services import store

router = APIRouter(prefix="/api/config", tags=["config"], dependencies=[Depends(require_token)])


@router.get("/mapping")
async def get_mapping() -> dict:
    mapping = await store.get_current_mapping()
    return mapping.model_dump()
