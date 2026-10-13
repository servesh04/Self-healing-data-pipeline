"""Preflight a complete batch, then persist it once with compare-and-swap."""
import logging
import hashlib
from pathlib import Path

from graph.nodes.multi_specialist import merge_fragments
from pipeline.contract import load_contract
from pipeline.mapping import MappingPatch
from pipeline.runner import run_pipeline
from services import store

log = logging.getLogger(__name__)


def merge_mapping(current: MappingPatch, patch: dict) -> MappingPatch:
    candidate = merge_fragments([patch])
    for section in ("renames", "casts", "null_policy"):
        existing = getattr(current, section)
        overlap = set(existing) & set(candidate.get(section, {}))
        if overlap:
            raise ValueError(f"patch rewrites existing {section}: {sorted(overlap)}")
    if set(current.drops) & set(candidate.get("drops", [])):
        raise ValueError("patch repeats an existing drop")
    maps = {col: dict(replacements) for col, replacements in current.value_maps.items()}
    for col, replacements in candidate.get("value_maps", {}).items():
        for source, target in replacements.items():
            if source in maps.get(col, {}):
                raise ValueError(f"patch rewrites existing value mapping: {col}.{source}")
        maps.setdefault(col, {}).update(replacements)
    return MappingPatch(
        renames={**current.renames, **candidate.get("renames", {})},
        casts={**current.casts, **candidate.get("casts", {})},
        null_policy={**current.null_policy, **candidate.get("null_policy", {})},
        drops=list(dict.fromkeys([*current.drops, *candidate.get("drops", [])])),
        value_maps=maps,
    )


async def apply_patch(state: dict) -> dict:
    patch = state.get("proposed_patch") or {}
    if not patch or not any(patch.values()):
        return {"patch_error": "empty patch", "applied_patch": None}
    try:
        current, version = await store.get_current_mapping_version()
        if version != state.get("mapping_version_read"):
            raise ValueError("mapping changed since this run started")
        source_hash = hashlib.sha256(Path(state["source_path"]).read_bytes()).hexdigest()
        if source_hash != state.get("source_sha256"):
            raise ValueError("source changed since this run started")
        merged = merge_mapping(current, patch)
        preflight = run_pipeline(state["source_path"], merged, load_contract())
        if hashlib.sha256(Path(state["source_path"]).read_bytes()).hexdigest() != source_hash:
            raise ValueError("source changed during preflight")
        if not preflight.passed:
            raise ValueError("preflight failed: " + "; ".join(preflight.as_strings()[:5]))
        written = await store.save_mapping_cas(
            merged, expected_id=version,
            updated_by=f"agent:attempt-{state['heal_attempts'] + 1}",
            run_id=state.get("run_id"),
        )
    except Exception as exc:
        log.warning("apply_patch: batch rejected: %s", exc)
        return {"patch_error": str(exc), "applied_patch": None,
                "mapping_version_written": None, "awaiting_approval": False}
    return {"heal_attempts": state["heal_attempts"] + 1,
            "applied_patch": patch, "mapping_version_written": written,
            "patch_error": None, "awaiting_approval": False}
