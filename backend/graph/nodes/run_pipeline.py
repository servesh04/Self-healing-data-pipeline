"""Real node — Phase 3, deterministic. Runs the actual pipeline
(backend/pipeline/runner.py, built in Phase 1) against the current mapping in
Postgres. `pipeline.runner.run_pipeline` already never raises internally, but
loading the mapping/contract here can still fail (e.g. Postgres unreachable);
this node must not let that escape either — ARCHITECTURE.md's "nodes must
never raise" guardrail.
"""

import logging
import hashlib
from pathlib import Path

from pipeline.contract import contract_for_state
from pipeline.runner import run_pipeline as run_the_pipeline
from services import store

log = logging.getLogger(__name__)


async def run_pipeline(state: dict) -> dict:
    try:
        contract = contract_for_state(state)
        mapping, mapping_version = await store.get_current_mapping_version(state.get("pipeline_env"), contract.name, contract)
        source_hash = hashlib.sha256(Path(state["source_path"]).read_bytes()).hexdigest()
        result = run_the_pipeline(state["source_path"], mapping, contract)
        if state.get("pipeline_env") and state.get("run_id"):
            await store.update_run_mapping_version(state["run_id"], mapping_version, "mapping_version_read")
        if hashlib.sha256(Path(state["source_path"]).read_bytes()).hexdigest() != source_hash:
            raise ValueError("source changed during initial validation")
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
        "mapping_version_read": mapping_version,
        "contract_name": contract.name,
        "contract_version": contract.version,
        "contract_snapshot": contract.spec,
        "baseline_failures": [{"column": f.column, "check": f.check, "failure_count": f.failure_count} for f in result.failures],
        "source_sha256": source_hash,
        "mapping_version_written": None,
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
