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
import hashlib
from pathlib import Path

from pipeline.contract import contract_for_state
from pipeline.runner import run_pipeline as run_the_pipeline
from services import store

log = logging.getLogger(__name__)


def _worsened(baseline: list[dict], current: list) -> bool:
    """A new stable check or higher violation count is a regression."""
    before = {(f["column"], f["check"]): int(f["failure_count"]) for f in baseline}
    return any(f.failure_count > before.get((f.column, f.check), 0) for f in current)


async def _record_outcomes(state: dict, passed: bool, detail: str | None) -> str | None:
    if not state.get("run_id"):
        return None
    try:
        for index in range(len(state.get("components") or [{}])):
            await store.record_component_event(state, index, "outcome", validation_passed=passed,
                mapping_version_id=state.get("mapping_version_written"), detail=detail)
    except Exception as exc:
        log.exception("component outcome audit failed")
        return f"component outcome audit failed: {exc}"
    return None


async def rerun_validate(state: dict) -> dict:
    rollback_version = None
    rollback_reason = None
    try:
        if hashlib.sha256(Path(state["source_path"]).read_bytes()).hexdigest() != state.get("source_sha256"):
            raise ValueError("source changed since preflight")
        contract = contract_for_state(state)
        mapping = await store.get_mapping_by_version(state["mapping_version_written"], state.get("pipeline_env"), contract.name, contract)
        result = run_the_pipeline(state["source_path"], mapping, contract)
        if hashlib.sha256(Path(state["source_path"]).read_bytes()).hexdigest() != state.get("source_sha256"):
            raise ValueError("source changed during rerun")
    except Exception as exc:
        log.exception("rerun_validate could not complete validation")
        failure_strs = [f"{type(exc).__name__}: {exc}"]
        rollback_reason = failure_strs[0]
        try:
            rollback_version = await store.rollback_mapping_cas(
                state["mapping_version_written"], state["run_id"], rollback_reason,
                state.get("pipeline_env"), state.get("contract_name", "default"), contract_for_state(state))
        except Exception as rollback_exc:
            rollback_reason += f"; rollback requires reconciliation: {rollback_exc}"
        attempt = {
            "attempt_no": state["heal_attempts"],
            "drift_summary": state.get("diagnosis") or "",
            "specialist": "+".join(c["specialist"] for c in state.get("components") or []),
            "components": state.get("components") or [],
            "mapping_version": state.get("mapping_version_written"),
            "rollback_version": rollback_version,
            "rollback_reason": rollback_reason,
            "patch": state.get("applied_patch") or {},
            "confidence": state.get("confidence") or 0.0,
            "approved_by": "human" if state.get("human_decision") else "auto",
            "result": "failed",
            "assertion_failures": failure_strs,
        }
        audit_error = await _record_outcomes(state, False, rollback_reason)
        return {
            "assertions_passed": False,
            "assertion_failures": failure_strs,
            "assertion_failure_details": [],
            "history": [attempt],
            "rollback_version": rollback_version,
            "rollback_reason": rollback_reason,
            "patch_error": rollback_reason,
            "audit_error": audit_error,
        }

    if not result.passed and _worsened(state.get("baseline_failures") or [], result.failures):
        rollback_reason = "rerun worsened stable failure checks: " + "; ".join(result.as_strings()[:5])
        try:
            rollback_version = await store.rollback_mapping_cas(
                state["mapping_version_written"], state["run_id"], rollback_reason,
                state.get("pipeline_env"), contract.name, contract)
        except Exception as exc:
            log.exception("rerun_validate rollback conflict")
            rollback_reason += f"; rollback requires reconciliation: {exc}"

    attempt = {
        "attempt_no": state["heal_attempts"],
        "drift_summary": state.get("diagnosis") or "",
        "specialist": "+".join(c["specialist"] for c in state.get("components") or []),
        "components": state.get("components") or [],
        "mapping_version": state.get("mapping_version_written"),
        "rollback_version": rollback_version,
        "rollback_reason": rollback_reason,
        "patch": state.get("applied_patch") or {},
        "confidence": state.get("confidence") or 0.0,
        "approved_by": "human" if state.get("human_decision") else "auto",
        "result": "passed" if result.passed else "failed",
        "assertion_failures": result.as_strings(),
    }

    audit_error = await _record_outcomes(state, result.passed, rollback_reason)
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
        "mapping_version_read": (state["mapping_version_written"]
                                 if not result.passed and rollback_reason is None
                                 else state.get("mapping_version_read")),
        "baseline_failures": ([{"column": f.column, "check": f.check,
                                "failure_count": f.failure_count} for f in result.failures]
                              if not result.passed and rollback_reason is None
                              else state.get("baseline_failures") or []),
        "rollback_version": rollback_version,
        "rollback_reason": rollback_reason,
        "patch_error": rollback_reason,
        "audit_error": audit_error,
    }
