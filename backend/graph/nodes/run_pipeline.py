"""Real node — Phase 3, deterministic. Runs the actual pipeline
(backend/pipeline/runner.py, built in Phase 1) against the current mapping in
Postgres. `pipeline.runner.run_pipeline` already never raises internally, but
loading the mapping/contract here can still fail (e.g. Postgres unreachable);
this node must not let that escape either — ARCHITECTURE.md's "nodes must
never raise" guardrail.
"""

import logging

from pipeline.contract import load_contract
from pipeline.runner import run_pipeline as run_the_pipeline
from services import store

log = logging.getLogger(__name__)


async def run_pipeline(state: dict) -> dict:
    try:
        mapping = await store.get_current_mapping()
        contract = load_contract()
        result = run_the_pipeline(state["source_path"], mapping, contract)
    except Exception as exc:
        log.exception("run_pipeline node failed before reaching the pipeline")
        return {
            "assertions_passed": False,
            "assertion_failures": [f"{type(exc).__name__}: {exc}"],
            "assertion_failure_details": [],
            "rows_in": 0,
            "rows_out": 0,
        }

    return {
        "assertions_passed": result.passed,
        "assertion_failures": result.as_strings(),
        "assertion_failure_details": [
            {
                "column": f.column,
                "check": f.check,
                "failure_count": f.failure_count,
                "example_values": f.example_values,
            }
            for f in result.failures
        ],
        "rows_in": result.rows_in,
        "rows_out": result.rows_out,
    }
