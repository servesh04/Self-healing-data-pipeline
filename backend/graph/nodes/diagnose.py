"""Real node — Phase 3. LLM classification (JSON mode) + Pydantic validation.

Confidence calibration here is the entire escalation story — see
prompts.py's DIAGNOSE_SYSTEM for the actual decision rules. Never raises: an
LLM or validation failure defaults to unknown/zero-confidence rather than
escaping (ARCHITECTURE.md's "nodes must never raise" guardrail) — "the LLM
call failed" and "the LLM was genuinely unsure" both belong on the same
escalation path, since neither one is safe to auto-apply.
"""

import json
import logging

from pydantic import ValidationError

from graph.schemas import DiagnoseOutput
from prompts.prompts import DIAGNOSE_SYSTEM, DIAGNOSE_USER_TEMPLATE
from services.llm import LLMError, call_json

log = logging.getLogger(__name__)


async def diagnose(state: dict) -> dict:
    user_prompt = DIAGNOSE_USER_TEMPLATE.format(
        drift_items=json.dumps(state.get("drift_items") or [], indent=2),
        assertion_failures=json.dumps(state.get("assertion_failures") or [], indent=2),
        source_profile=json.dumps(state.get("source_profile") or {}, indent=2),
    )

    try:
        raw = await call_json(DIAGNOSE_SYSTEM, user_prompt)
        parsed = DiagnoseOutput.model_validate(raw)
    except (LLMError, ValidationError) as exc:
        log.warning("diagnose: failed, defaulting to unknown/low-confidence: %s", exc)
        return {
            "drift_class": "unknown",
            "diagnosis": f"diagnose failed ({type(exc).__name__}) -- escalating rather than guessing",
            "confidence": 0.0,
        }

    return {
        "drift_class": parsed.drift_class,
        "diagnosis": parsed.diagnosis,
        "confidence": parsed.confidence,
    }
