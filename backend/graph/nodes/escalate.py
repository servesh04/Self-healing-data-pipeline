"""Terminal: needs a human. Never fabricates success — says what drifted,
what was tried, and why it stopped, using whatever state accumulated
(history, drift_class, diagnosis).
"""


def escalate(state: dict) -> dict:
    tried = len(state.get("history", []))
    if state.get("human_decision") == "reject":
        reason = "human rejected the proposed patch"
    elif state.get("drift_class") == "unknown":
        diagnosis = state.get("diagnosis")
        reason = f"unclassifiable drift ({diagnosis})" if diagnosis else "unclassifiable drift"
    else:
        reason = f"exhausted {tried} heal attempt(s) without passing"
    return {
        "status": "escalated",
        "final_message": f"escalated -- {reason}",
    }
