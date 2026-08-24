"""Real node — Phase 3. LLM specialist scoped to the `casts` section only.

Only ever reached when diagnose has already classified the drift as "type".
Its raw output is validated against TypeSpecialistOutput — extra="forbid",
plus registry-checked operation names — before anything downstream sees it.
See spec_rename.py's docstring for why this per-specialist validation, not
just the general MappingPatch check, is what actually catches scope leakage,
and for why candidates are normalized via normalize_llm_json (first valid
one wins — no confidence field to tie-break on here).
"""

import json
import logging

from pydantic import ValidationError

from graph.schemas import TypeSpecialistOutput, normalize_llm_json
from pipeline.mapping import CAST_REGISTRY
from prompts.prompts import SPEC_TYPE_SYSTEM, SPEC_TYPE_USER_TEMPLATE
from services.llm import LLMError, call_json

log = logging.getLogger(__name__)


async def spec_type(state: dict) -> dict:
    system_prompt = SPEC_TYPE_SYSTEM.replace(
        "%%CAST_REGISTRY%%", json.dumps(sorted(CAST_REGISTRY), indent=2)
    )
    user_prompt = SPEC_TYPE_USER_TEMPLATE.format(
        drift_items=json.dumps(state.get("drift_items") or [], indent=2),
        source_profile=json.dumps(state.get("source_profile") or {}, indent=2),
        diagnosis=state.get("diagnosis") or "",
    )

    try:
        raw = await call_json(system_prompt, user_prompt)
    except LLMError as exc:
        log.warning("spec_type: LLM call failed: %s", exc)
        return {"specialist_output": {}}

    for candidate in normalize_llm_json(raw):
        try:
            parsed = TypeSpecialistOutput.model_validate(candidate)
            return {"specialist_output": parsed.model_dump()}
        except ValidationError as exc:
            log.warning("spec_type: candidate failed validation (invalid or out-of-scope): %s", exc)

    return {"specialist_output": {}}
