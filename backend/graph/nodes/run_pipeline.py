"""STUB — Phase 2. Real pipeline execution (backend/pipeline/runner.py, built
in Phase 1) is wired in from Phase 3. Every branch here is a hardcoded dict,
selected by the scenario encoded in source_path — see
_phase2_stub_scenarios.py. Zero LLM calls, zero file IO.
"""

from graph.nodes._phase2_stub_scenarios import scenario_of


def run_pipeline(state: dict) -> dict:
    scenario = scenario_of(state)
    if scenario == "clean":
        return {
            "assertions_passed": True,
            "assertion_failures": [],
            "rows_in": 50,
            "rows_out": 50,
        }
    return {
        "assertions_passed": False,
        "assertion_failures": ["stub: revenue dtype mismatch"],
        "rows_in": 50,
        "rows_out": 50,
    }
