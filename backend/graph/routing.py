"""Conditional-edge routers. Pure: read state, return a string, never mutate.

ARCHITECTURE.md names four routers in its rubric mapping — post-run,
post-diagnose, post-confidence, post-rerun — and that is exactly what the
first four functions below are. `route_after_human_approval` is a fifth,
necessary conditional edge the topology diagram itself requires (the
approve/reject fork after `human_approval`); it isn't one of the rubric's
named four because it dispatches the *human's* decision rather than the
pipeline/agent's own state, but LangGraph still needs a callable to wire that
fork, so it lives here alongside the rest.

`heal_attempts` is incremented inside `apply_patch`, never in a router — see
that node and the guardrail in ARCHITECTURE.md's Appendix A. Every function
below only reads it.
"""

from graph.state import CONFIDENCE_THRESHOLD, MAX_HEAL_ATTEMPTS, HealState


def route_after_run(state: HealState) -> str:
    return "commit_report" if state["assertions_passed"] else "profile_source"


def route_after_diagnose(state: HealState) -> str:
    if state["drift_class"] in ("rename", "type", "nullability"):
        return f"spec_{state['drift_class']}"
    return "escalate"


def route_after_confidence(state: HealState) -> str:
    if (state["confidence"] or 0.0) >= CONFIDENCE_THRESHOLD:
        return "apply_patch"
    return "human_approval"


def route_after_rerun(state: HealState) -> str:
    if state["assertions_passed"]:
        return "commit_report"
    if state["heal_attempts"] >= MAX_HEAL_ATTEMPTS:
        return "escalate"
    return "diff_contract"  # ← THE HEAL CYCLE


def route_after_human_approval(state: HealState) -> str:
    return "apply_patch" if state.get("human_decision") == "approve" else "escalate"
