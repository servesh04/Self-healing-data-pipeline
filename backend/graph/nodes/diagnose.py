"""STUB — Phase 2. Real LLM classification (JSON mode + Pydantic validation)
arrives in Phase 3.

Cycles through the three drift classes across successive heal attempts
(heal_attempts is 0 on the first pass, 1 after the first apply_patch, ...),
so a single "heals-in-3" or "never-heals" scenario run exercises every
specialist branch of route_after_diagnose without needing three separate
scenarios for that alone.
"""

from graph.nodes._phase2_stub_scenarios import scenario_of

_CYCLE = ["rename", "type", "nullability"]


def diagnose(state: dict) -> dict:
    scenario = scenario_of(state)

    if scenario == "unknown-drift":
        return {
            "drift_class": "unknown",
            "diagnosis": "stub: two unrelated drifts, ambiguous mapping",
            "confidence": 0.2,
        }

    if scenario.startswith("low-confidence"):
        return {
            "drift_class": "rename",
            "diagnosis": "stub: plausible rename but low confidence",
            "confidence": 0.3,
        }

    drift_class = _CYCLE[state["heal_attempts"] % len(_CYCLE)]
    return {
        "drift_class": drift_class,
        "diagnosis": f"stub: classified as {drift_class}, attempt {state['heal_attempts'] + 1}",
        "confidence": 0.9,
    }
