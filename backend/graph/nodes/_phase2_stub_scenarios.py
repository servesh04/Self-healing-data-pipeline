"""Phase 2 scaffolding ONLY — not part of ARCHITECTURE.md's design.

Every stub node in this package reads `state["source_path"]` as a scenario
selector rather than loading a real file — there is no real pipeline call
until Phase 3. Prefixing the selector onto the field the real pipeline will
eventually use (source_path) avoids adding an undocumented field to
HealState just for Phase 2's own testing.

Delete or ignore this module once Phase 3 replaces run_pipeline,
profile_source, diff_contract, diagnose, and rerun_validate with real logic —
the "stub://..." strings are not meant to survive past Phase 2, and nothing
outside this package should ever construct one.
"""

SCENARIO_PREFIX = "stub://"


def scenario_of(state: dict) -> str:
    path = state.get("source_path", "")
    if path.startswith(SCENARIO_PREFIX):
        return path[len(SCENARIO_PREFIX) :]
    return "heals-in-3"  # default: demonstrate the full heal cycle
