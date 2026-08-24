"""`heal_attempts` is incremented HERE and only here — never in a router (see
ARCHITECTURE.md's guardrail). This is real logic even in Phase 2, not a
hardcoded stub: the counter must reflect actual prior state for the
MAX_HEAL_ATTEMPTS cap and the heal-cycle demonstration to mean anything.

Real mapping persistence (backend/services/store.py, built in Phase 1)
arrives in Phase 3/4 — this stub records the (hardcoded) proposed patch as
applied but does not yet touch Postgres.
"""


def apply_patch(state: dict) -> dict:
    return {
        "heal_attempts": state["heal_attempts"] + 1,
        "applied_patch": state.get("proposed_patch") or {},
        "awaiting_approval": False,
    }
