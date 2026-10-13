"""Categorical value specialist, scoped to observed enum failures."""
import json
import logging

from pydantic import ValidationError

from graph.schemas import ValueSpecialistOutput, normalize_llm_json
from pipeline.contract import load_contract
from prompts.prompts import SPEC_VALUE_SYSTEM, SPEC_VALUE_USER_TEMPLATE
from services.llm import LLMError, call_json
from services import store

log = logging.getLogger(__name__)


def _mechanical_alias(source: str, target: str, allowed: list[str]) -> bool:
    if source.casefold() == target.casefold():
        return True
    initials = [label for label in allowed if label and label[0].casefold() == source.casefold()]
    return len(source) == 1 and initials == [target]


async def spec_value(state: dict) -> dict:
    findings = state.get("drift_items") or []
    profile = state.get("source_profile") or {}
    try:
        current = await store.get_current_mapping()
        aliases = {**current.renames, **(state.get("pending_renames") or {})}
        profile = {**profile, **{new: profile[old] for old, new in aliases.items() if old in profile}}
    except Exception:
        return {"specialist_output": {}}
    allowed = load_contract().allowed_values
    if not findings or any(f.get("kind") != "enum_value" or f.get("column") not in allowed
                           or profile.get(f["column"], {}).get("truncated") for f in findings):
        return {"specialist_output": {}}
    try:
        raw = await call_json(SPEC_VALUE_SYSTEM, SPEC_VALUE_USER_TEMPLATE.format(
            drift_items=json.dumps(findings),
            source_profile=json.dumps({f["column"]: profile.get(f["column"], {}) for f in findings}),
            allowed_values=json.dumps({f["column"]: allowed[f["column"]] for f in findings}),
        ))
    except LLMError as exc:
        log.warning("spec_value: LLM call failed: %s", exc)
        return {"specialist_output": {}}
    for item in normalize_llm_json(raw):
        try:
            patch = ValueSpecialistOutput.model_validate(item).model_dump()
            maps = patch["value_maps"]
            if set(maps) != {f["column"] for f in findings}:
                continue
            for f in findings:
                col = f["column"]
                frequencies = profile[col]["frequencies"]
                unexpected = set(frequencies) - set(allowed[col])
                if not unexpected or set(maps[col]) != unexpected:
                    raise ValueError("incomplete or extra value mapping")
                if set(maps[col].values()) - set(allowed[col]):
                    raise ValueError("target outside allowed set")
                if any(not _mechanical_alias(source, target, allowed[col])
                       for source, target in maps[col].items()):
                    raise ValueError("categorical change is not a unique mechanical alias")
            return {"specialist_output": patch}
        except (ValidationError, ValueError, KeyError) as exc:
            log.warning("spec_value: rejected candidate: %s", exc)
    return {"specialist_output": {}}
