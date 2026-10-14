"""Terminal: success. Distinguishes a clean first-run pass from one reached
after healing, using the real heal_attempts counter.
"""


def commit_report(state: dict) -> dict:
    if state.get("monitor_error"):
        return {"status": "escalated", "final_message": state["monitor_error"]}
    healed = state["heal_attempts"] > 0
    alerts = state.get("distribution_alerts") or []
    if alerts:
        columns = ", ".join(sorted({a["column"] for a in alerts}))
        return {"status": "alert", "final_message": f"distribution alert: {columns}; schema validation passed"}
    return {
        "status": "healed" if healed else "clean",
        "final_message": (
            f"healed after {state['heal_attempts']} attempt(s)"
            if healed
            else "no drift detected"
        ),
    }
