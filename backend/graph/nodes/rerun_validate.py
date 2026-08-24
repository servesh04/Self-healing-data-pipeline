"""STUB — Phase 2. Real pipeline rerun (backend/pipeline/runner.py) — THE
ORACLE — arrives in Phase 3. Here the pass/fail outcome is scripted per
scenario so the heal cycle can be demonstrated deterministically:

  "heals-in-3"   — fails on attempts 1-2, passes on attempt 3
  "never-heals"  — always fails (demonstrates the MAX_HEAL_ATTEMPTS cap)
  anything else  — passes immediately (e.g. the low-confidence-approve path)

Appends exactly one HealAttempt to `history` per call — this is what proves
the `Annotated[..., operator.add]` reducer actually appends rather than
overwrites (the Phase 2 Definition of Done item most worth watching closely).
"""

from graph.nodes._phase2_stub_scenarios import scenario_of


def rerun_validate(state: dict) -> dict:
    scenario = scenario_of(state)
    attempt_no = state["heal_attempts"]

    if scenario == "heals-in-3":
        passed = attempt_no >= 3
    elif scenario == "never-heals":
        passed = False
    else:
        passed = True

    failures = [] if passed else ["stub: revenue still fails dtype check"]
    attempt = {
        "attempt_no": attempt_no,
        "drift_summary": state.get("diagnosis") or "stub",
        "specialist": f"spec_{state.get('drift_class')}",
        "patch": state.get("applied_patch") or {},
        "confidence": state.get("confidence") or 0.0,
        "approved_by": "human" if state.get("human_decision") else "auto",
        "result": "passed" if passed else "failed",
        "assertion_failures": failures,
    }
    return {
        "assertions_passed": passed,
        "assertion_failures": failures,
        "history": [attempt],
    }
