"""Check a validated batch for alert-only distribution drift."""

import logging

from pipeline.contract import contract_for_state
from pipeline.distribution import check_distribution
from pipeline.sources import ensure_source_path
from services import store

log = logging.getLogger(__name__)


async def monitor_distribution(state: dict) -> dict:
    try:
        contract = contract_for_state(state)
        await ensure_source_path(state)
        version = state.get("mapping_version_written") or state.get("mapping_version_read")
        mapping = await store.get_mapping_by_version(version, state.get("pipeline_env"), contract.name, contract)
        result = check_distribution(state["source_path"], mapping, contract.name,
                                    state.get("source_family"), pinned_baseline=state.get("distribution_baseline"))
        return {"distribution_alerts": result.alerts, "distribution_skipped": result.skipped,
                "monitor_completed": True,
                "distribution_profile": result.profile, "distribution_baseline_id": result.baseline_id,
                "distribution_baseline_version": result.baseline_version,
                "distribution_baseline_digest": result.baseline_digest}
    except Exception as exc:
        log.exception("distribution monitor failed")
        return {"distribution_alerts": [], "monitor_completed": True,
                "monitor_error": f"distribution monitor failed: {exc}"}
