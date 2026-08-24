"""Real node — Phase 3/4. Backs up the current mapping, merges the patch,
persists it (ARCHITECTURE.md's node spec) — never to the container
filesystem, always to Postgres (backend/services/store.py).

`heal_attempts` is incremented HERE and only here — never in a router (see
ARCHITECTURE.md's guardrail). This must reflect actual prior state for the
MAX_HEAL_ATTEMPTS cap and the heal-cycle demonstration to mean anything.

"Backs up the current mapping" falls out of store.py's append-only design:
save_mapping() never overwrites, it inserts a new row, so the pre-patch
mapping is simply the previous row and is never lost.

The patch is a FRAGMENT (e.g. just `{"renames": {...}}`), not a full
mapping — merging means taking the current mapping and updating only the
section(s) the patch names, not replacing the whole thing.
"""

import logging

from pipeline.mapping import MappingPatch
from services import store

log = logging.getLogger(__name__)


async def apply_patch(state: dict) -> dict:
    patch = state.get("proposed_patch") or {}

    try:
        current = await store.get_current_mapping()
        merged = MappingPatch(
            renames={**current.renames, **patch.get("renames", {})},
            casts={**current.casts, **patch.get("casts", {})},
            null_policy={**current.null_policy, **patch.get("null_policy", {})},
            # dict.fromkeys dedupes while preserving order; a patch re-naming
            # an already-dropped column shouldn't create a duplicate entry.
            drops=list(dict.fromkeys([*current.drops, *patch.get("drops", [])])),
        )
        await store.save_mapping(
            merged,
            updated_by=f"agent:attempt-{state['heal_attempts'] + 1}",
            run_id=state.get("run_id"),
        )
    except Exception:
        # Persisting the patch failed (e.g. Postgres unreachable, or the
        # merged patch failed MappingPatch's own validation — shouldn't
        # happen given propose_patch's schema, but defense in depth). The
        # attempt still counts: rerun_validate will observe the mapping
        # unchanged and correctly report a failed attempt, rather than this
        # node raising and killing the run.
        log.exception("apply_patch: failed to persist the merged mapping")

    return {
        "heal_attempts": state["heal_attempts"] + 1,
        "applied_patch": patch,
        "awaiting_approval": False,
    }
