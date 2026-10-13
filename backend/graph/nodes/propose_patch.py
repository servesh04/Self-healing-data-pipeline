"""Real node — Phase 3. Wraps the specialist's already-validated, narrowly-
scoped output as the proposed patch, and writes a one-sentence rationale via
a small LLM call.

The patch content itself is never re-decided here — the specialist already
produced and validated it (see spec_rename.py et al.). propose_patch's LLM
role is limited to the rationale; ProposePatchOutput validates ONLY that raw
response (extra="forbid", just `rationale`), so if the model tries to invent
its own patch content instead of describing the specialist's, that fails
validation immediately rather than silently overriding it. Candidates are
normalized via normalize_llm_json (see graph/schemas.py) the same as every
other LLM node — first valid one wins, no confidence field here either.
"""

import logging
import re

from pydantic import ValidationError

from graph.schemas import ProposePatchOutput, normalize_llm_json
from prompts.prompts import PROPOSE_PATCH_SYSTEM, PROPOSE_PATCH_USER_TEMPLATE
from services.llm import LLMError, call_json

log = logging.getLogger(__name__)

RENAME_REVIEW_CAP = 0.4


def _obvious_rename(source: str, target: str) -> bool:
    """Require a meaningful shared token/stem before trusting rename confidence."""
    source_tokens = re.findall(r"[A-Za-z]+", source.casefold())
    target_tokens = re.findall(r"[A-Za-z]+", target.casefold())
    return any(len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4]
               for a in source_tokens for b in target_tokens)


async def propose_patch(state: dict) -> dict:
    patch = state.get("specialist_output") or {}
    if state.get("proposal_error"):
        return {"proposed_patch": None, "patch_rationale": state["proposal_error"]}
    if not patch or not any(patch.values()):
        return {"proposed_patch": None, "proposal_error": "specialist returned an empty patch"}
    if not state.get("components"):
        candidate = (state.get("candidates") or [{}])[0]
        components = [{**candidate, "specialist": f"spec_{state.get('drift_class')}", "patch": patch}]
    else:
        components = state["components"]
    components = [dict(component) for component in components]
    for component in components:
        if component.get("drift_class") != "rename":
            continue
        renames = component.get("patch", {}).get("renames", {})
        if any(not _obvious_rename(source, target) for source, target in renames.items()):
            component["confidence"] = min(component.get("confidence", 0.0), RENAME_REVIEW_CAP)
            component["safety_status"] = "review: weak lexical rename match"
    confidence = min(state.get("confidence") or 0.0,
                     *(component.get("confidence", 0.0) for component in components))
    user_prompt = PROPOSE_PATCH_USER_TEMPLATE.format(
        diagnosis=state.get("diagnosis") or "",
        drift_class=state.get("drift_class") or "",
        patch=patch,
    )

    rationale = f"proposed a {state.get('drift_class')} fix (rationale generation failed)"
    try:
        raw = await call_json(PROPOSE_PATCH_SYSTEM, user_prompt)
        for candidate in normalize_llm_json(raw):
            try:
                rationale = ProposePatchOutput.model_validate(candidate).rationale
                break
            except ValidationError as exc:
                log.warning("propose_patch: candidate failed validation: %s", exc)
    except LLMError as exc:
        log.warning("propose_patch: rationale generation failed, using a template rationale: %s", exc)

    return {
        "proposed_patch": patch,
        "patch_rationale": rationale,
        "components": components,
        "confidence": confidence,
    }
