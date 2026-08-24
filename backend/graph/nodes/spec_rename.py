"""Real node — Phase 3. LLM specialist scoped to the `renames` section only.

Only ever reached when diagnose has already classified the drift as
"rename" (route_after_diagnose). Its raw output is validated against
RenameSpecialistOutput — extra="forbid" — before anything downstream ever
sees it. This is where specialist scope leakage is actually caught: if the
model emits a stray "casts" key alongside "renames", validation fails right
here, by name, rather than three nodes later where it would just look like a
small unrelated patch fragment quietly riding along.

Also normalizes via normalize_llm_json (see graph/schemas.py) — the model
has been observed to sometimes wrap its answer as a list of candidates
rather than one object; unlike diagnose, there's no confidence field to
break ties on here, so the first candidate that validates wins.
"""

import json
import logging

from pydantic import ValidationError

from graph.schemas import RenameSpecialistOutput, normalize_llm_json
from prompts.prompts import SPEC_RENAME_SYSTEM, SPEC_RENAME_USER_TEMPLATE
from services.llm import LLMError, call_json

log = logging.getLogger(__name__)


async def spec_rename(state: dict) -> dict:
    user_prompt = SPEC_RENAME_USER_TEMPLATE.format(
        drift_items=json.dumps(state.get("drift_items") or [], indent=2),
        source_profile=json.dumps(state.get("source_profile") or {}, indent=2),
        diagnosis=state.get("diagnosis") or "",
    )

    try:
        raw = await call_json(SPEC_RENAME_SYSTEM, user_prompt)
    except LLMError as exc:
        log.warning("spec_rename: LLM call failed: %s", exc)
        return {"specialist_output": {}}

    for candidate in normalize_llm_json(raw):
        try:
            parsed = RenameSpecialistOutput.model_validate(candidate)
            return {"specialist_output": parsed.model_dump()}
        except ValidationError as exc:
            log.warning("spec_rename: candidate failed validation (invalid or out-of-scope): %s", exc)

    return {"specialist_output": {}}
