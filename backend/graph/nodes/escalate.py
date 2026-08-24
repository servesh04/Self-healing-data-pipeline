"""Terminal: needs a human. Never fabricates success — says what drifted,
what was tried, and why it stopped, using whatever state accumulated
(history, drift_class), even in Phase 2's stub form.
"""


def escalate(state: dict) -> dict:
    tried = len(state.get("history", []))
    if state.get("human_decision") == "reject":
        reason = "human rejected the proposed patch"
    elif state.get("drift_class") == "unknown":
        reason = "unclassifiable drift (ambiguous mapping)"
    else:
        reason = f"exhausted {tried} heal attempt(s) without passing"
    return {
        "status": "escalated",
        "final_message": f"stub: escalated -- {reason}",
    }
