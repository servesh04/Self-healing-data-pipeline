"""Real node — Phase 3. LLM specialist scoped to the `null_policy` section
only.

Only ever reached when diagnose has already classified the drift as
"nullability". Its raw output is validated against
NullabilitySpecialistOutput — extra="forbid", plus registry-checked policy
names — before anything downstream sees it. See spec_rename.py's docstring
for why this per-specialist validation is what actually catches scope
leakage, not just the general MappingPatch check.
"""

import json
import logging

from pydantic import ValidationError

from graph.schemas import NullabilitySpecialistOutput
from pipeline.mapping import NULL_POLICY_REGISTRY
from prompts.prompts import SPEC_NULLABILITY_SYSTEM, SPEC_NULLABILITY_USER_TEMPLATE
from services.llm import LLMError, call_json

log = logging.getLogger(__name__)


async def spec_nullability(state: dict) -> dict:
    system_prompt = SPEC_NULLABILITY_SYSTEM.replace(
        "%%NULL_POLICY_REGISTRY%%", json.dumps(sorted(NULL_POLICY_REGISTRY), indent=2)
    )
    user_prompt = SPEC_NULLABILITY_USER_TEMPLATE.format(
        drift_items=json.dumps(state.get("drift_items") or [], indent=2),
        source_profile=json.dumps(state.get("source_profile") or {}, indent=2),
        diagnosis=state.get("diagnosis") or "",
    )

    try:
        raw = await call_json(system_prompt, user_prompt)
        parsed = NullabilitySpecialistOutput.model_validate(raw)
    except (LLMError, ValidationError) as exc:
        log.warning("spec_nullability: failed or emitted an invalid/out-of-scope patch: %s", exc)
        return {"specialist_output": {}}

    return {"specialist_output": parsed.model_dump()}
