"""STUB — Phase 2. Real LLM merge of specialist output into a validated
MappingPatch fragment (backend/pipeline/mapping.py) arrives in Phase 3.

Picks a hardcoded, syntactically-valid mapping fragment keyed by drift_class —
purely a lookup, no LLM — but shaped exactly like a real MappingPatch section
so Phase 3 only has to replace the lookup with a model call plus validation.
"""

_STUB_PATCHES = {
    "rename": {"renames": {"stub_old_col": "stub_new_col"}},
    "type": {"casts": {"stub_col": "strip_commas_to_float"}},
    "nullability": {"null_policy": {"stub_col": "fill_unknown"}},
}


def propose_patch(state: dict) -> dict:
    patch = _STUB_PATCHES.get(state["drift_class"], {})
    return {
        "proposed_patch": patch,
        "patch_rationale": f"stub: proposed {state['drift_class']} fix",
    }
