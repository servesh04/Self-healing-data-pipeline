"""Terminal: success. Distinguishes a clean first-run pass from one reached
after healing, using the real heal_attempts counter.
"""


def commit_report(state: dict) -> dict:
    healed = state["heal_attempts"] > 0
    return {
        "status": "healed" if healed else "clean",
        "final_message": (
            f"healed after {state['heal_attempts']} attempt(s)"
            if healed
            else "no drift detected"
        ),
    }
