"""Evaluate a validated batch with the exact temporal rule pinned at trigger."""

import logging

from pipeline.contract import contract_for_state
from pipeline.sources import ensure_source_path
from pipeline.temporal import check_temporal
from services import store

log = logging.getLogger(__name__)


async def monitor_temporal(state: dict) -> dict:
    try:
        if state.get("temporal_rule") is None:
            return {"temporal_alerts": [], "temporal_skipped": [{"reason": "no temporal rule"}],
                    "temporal_monitor_completed": True}
        if not state.get("run_clock_utc"):
            raise ValueError("pinned UTC run clock is missing")
        contract = contract_for_state(state)
        await ensure_source_path(state)
        version = state.get("mapping_version_written") or state.get("mapping_version_read")
        mapping = await store.get_mapping_by_version(version, state.get("pipeline_env"), contract.name, contract)
        result = check_temporal(state["source_path"], mapping, contract.name,
                                state.get("source_family"), state["run_clock_utc"],
                                pinned_rule=state.get("temporal_rule"))
        return {"temporal_alerts": result.alerts, "temporal_skipped": result.skipped,
                "temporal_profile": result.profile, "temporal_rule_digest": result.rule_digest,
                "temporal_monitor_completed": True}
    except Exception as exc:
        log.exception("temporal monitor failed")
        return {"temporal_alerts": [], "temporal_monitor_completed": True,
                "temporal_monitor_error": f"temporal monitor failed: {exc}"}
