"""Real node — Phase 3, deterministic. THE ORACLE. Reruns the actual
pipeline with the just-patched mapping in Postgres. Pandera passes or fails —
ground truth, not an LLM opinion. Appends a HealAttempt to history.

Populates `assertion_failure_details` from this same run, same as
run_pipeline — diff_contract reads whichever of the two most recently ran.
This is the mechanism that makes the drift signal narrow correctly between
heal attempts (see diff_contract.py's docstring); a stale or partial update
here would break that.
"""

import logging

from pipeline.contract import load_contract
from pipeline.runner import run_pipeline as run_the_pipeline
from services import store

log = logging.getLogger(__name__)


async def rerun_validate(state: dict) -> dict:
    try:
        mapping = await store.get_current_mapping()
        contract = load_contract()
        result = run_the_pipeline(state["source_path"], mapping, contract)
    except Exception as exc:
        log.exception("rerun_validate node failed before reaching the pipeline")
        failure_strs = [f"{type(exc).__name__}: {exc}"]
        attempt = {
            "attempt_no": state["heal_attempts"],
            "drift_summary": state.get("diagnosis") or "",
            "specialist": f"spec_{state.get('drift_class')}",
            "patch": state.get("applied_patch") or {},
            "confidence": state.get("confidence") or 0.0,
            "approved_by": "human" if state.get("human_decision") else "auto",
            "result": "failed",
            "assertion_failures": failure_strs,
        }
        return {
            "assertions_passed": False,
            "assertion_failures": failure_strs,
            "assertion_failure_details": [],
            "history": [attempt],
        }

    attempt = {
        "attempt_no": state["heal_attempts"],
        "drift_summary": state.get("diagnosis") or "",
        "specialist": f"spec_{state.get('drift_class')}",
        "patch": state.get("applied_patch") or {},
        "confidence": state.get("confidence") or 0.0,
        "approved_by": "human" if state.get("human_decision") else "auto",
        "result": "passed" if result.passed else "failed",
        "assertion_failures": result.as_strings(),
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
        "rows_out": result.rows_out,
        "history": [attempt],
    }
