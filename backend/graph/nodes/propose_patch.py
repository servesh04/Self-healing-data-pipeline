"""Real node — Phase 3. Wraps the specialist's already-validated, narrowly-
scoped output as the proposed patch, and writes a one-sentence rationale via
a small LLM call.

The patch content itself is never re-decided here — the specialist already
produced and validated it (see spec_rename.py et al.). propose_patch's LLM
role is limited to the rationale; ProposePatchOutput validates ONLY that raw
response (extra="forbid", just `rationale`), so if the model tries to invent
its own patch content instead of describing the specialist's, that fails
validation immediately rather than silently overriding it.
"""

import logging

from pydantic import ValidationError

from graph.schemas import ProposePatchOutput
from prompts.prompts import PROPOSE_PATCH_SYSTEM, PROPOSE_PATCH_USER_TEMPLATE
from services.llm import LLMError, call_json

log = logging.getLogger(__name__)


async def propose_patch(state: dict) -> dict:
    patch = state.get("specialist_output") or {}
    user_prompt = PROPOSE_PATCH_USER_TEMPLATE.format(
        diagnosis=state.get("diagnosis") or "",
        drift_class=state.get("drift_class") or "",
        patch=patch,
    )

    try:
        raw = await call_json(PROPOSE_PATCH_SYSTEM, user_prompt)
        parsed = ProposePatchOutput.model_validate(raw)
        rationale = parsed.rationale
    except (LLMError, ValidationError) as exc:
        log.warning("propose_patch: rationale generation failed, using a template rationale: %s", exc)
        rationale = f"proposed a {state.get('drift_class')} fix (rationale generation failed)"

    return {
        "proposed_patch": patch,
        "patch_rationale": rationale,
    }
