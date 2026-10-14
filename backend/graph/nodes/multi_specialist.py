"""Compose a diagnosed batch, requiring every component to succeed."""
from graph.nodes.spec_rename import spec_rename
from graph.nodes.spec_type import spec_type
from graph.nodes.spec_nullability import spec_nullability
from graph.nodes.spec_value import spec_value
from graph.nodes.spec_date_parse import spec_date_parse
from pipeline.mapping import MappingPatch
from pipeline.contract import Contract, contract_for_state

SPECIALISTS = {"rename": spec_rename, "type": spec_type,
               "nullability": spec_nullability, "value": spec_value, "date_parse": spec_date_parse}


def merge_fragments(fragments: list[dict], renames: dict[str, str] | None = None,
                    contract: Contract | None = None) -> dict:
    """Normalize post-rename names and reject duplicate or conflicting writes."""
    renames = renames or {}
    merged = {"renames": {}, "casts": {}, "null_policy": {}, "value_maps": {}, "date_parse": {}, "drops": []}
    for fragment in fragments:
        MappingPatch.model_validate(fragment, context={"contract": contract} if contract else None)
        for section in ("renames", "casts", "null_policy", "value_maps", "date_parse"):
            for key, value in fragment.get(section, {}).items():
                normalized = renames.get(key, key) if section != "renames" else key
                if section == "value_maps" and normalized in merged[section]:
                    if set(merged[section][normalized]) & set(value):
                        raise ValueError(f"duplicate value mapping: {normalized}")
                    merged[section][normalized].update(value)
                elif normalized in merged[section]:
                    raise ValueError(f"duplicate {section} write: {normalized}")
                else:
                    merged[section][normalized] = value
        for key in fragment.get("drops", []):
            if key in merged["drops"]:
                raise ValueError(f"duplicate drop: {key}")
            merged["drops"].append(key)
    if set(merged["renames"].values()) & set(merged["renames"]):
        raise ValueError("chained renames are not supported")
    for section in ("casts", "null_policy", "value_maps", "date_parse"):
        if set(merged[section]) & set(merged["drops"]):
            raise ValueError("operation targets a dropped column")
    return {k: v for k, v in merged.items() if v}


async def multi_specialist(state: dict) -> dict:
    if state.get("drift_class") == "unknown":
        return {"proposal_error": "unknown diagnosis", "specialist_outputs": None}
    fragments, components = [], []
    ordered = sorted(state.get("candidates") or [], key=lambda c: c["drift_class"] != "rename")
    for candidate in ordered:
        cls = candidate["drift_class"]
        scoped = dict(state)
        scoped["drift_items"] = [f for f in state.get("drift_items", [])
                                 if f["finding_id"] in candidate["finding_ids"]]
        scoped["pending_renames"] = {k: v for f in fragments for k, v in f.get("renames", {}).items()}
        try:
            fragment = (await SPECIALISTS[cls](scoped)).get("specialist_output") or {}
            if not fragment or not any(fragment.values()):
                raise ValueError(f"{cls} returned an empty proposal")
            fragments.append(fragment)
            components.append({**candidate, "specialist": f"spec_{cls}", "patch": fragment})
        except Exception as exc:
            return {"proposal_error": str(exc), "specialist_outputs": None,
                    "specialist_output": None, "components": []}
    try:
        renames = {k: v for f in fragments for k, v in f.get("renames", {}).items()}
        merged = merge_fragments(fragments, renames, contract_for_state(state))
    except Exception as exc:
        return {"proposal_error": str(exc), "specialist_outputs": None,
                "specialist_output": None, "components": []}
    return {"specialist_outputs": fragments, "specialist_output": merged,
            "components": components, "proposal_error": None}
