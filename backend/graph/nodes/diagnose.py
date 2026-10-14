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

from graph.schemas import DiagnoseOutput, normalize_llm_json
from prompts.prompts import DIAGNOSE_SYSTEM, DIAGNOSE_USER_TEMPLATE, DIAGNOSE_REPAIR_USER_TEMPLATE
from services.llm import LLMError, call_json
from graph.nodes.spec_date_parse import spec_date_parse

log = logging.getLogger(__name__)

_DEFAULT_UNKNOWN = {
    "drift_class": "unknown",
    "diagnosis": "diagnose returned no valid classification -- escalating rather than guessing",
    "confidence": 0.0,
    "candidates": [],
    "specialist_output": None,
    "specialist_outputs": None,
    "proposed_patch": None,
    "applied_patch": None,
    "components": [],
    "proposal_error": None,
    "patch_error": None,
}


def _candidate_error(candidates: list[dict], findings: list[dict]) -> str | None:
    """Require exact, disjoint coverage and mechanically admissible groups."""
    ids = {item["finding_id"] for item in findings}
    covered = [fid for c in candidates for fid in c["finding_ids"]]
    if not candidates or len(covered) != len(set(covered)) or set(covered) != ids:
        return "coverage"
    for c in candidates:
        kinds = [i["kind"] for i in findings if i["finding_id"] in c["finding_ids"]]
        acceptable = {"rename": {"missing_column", "unexpected_column"},
                      "type": {"dtype_mismatch"}, "nullability": {"null_violation"},
                      "value": {"enum_value"},
                      "date_parse": {"null_violation", "dtype_mismatch"}}[c["drift_class"]]
        if set(kinds) - acceptable or (c["drift_class"] == "rename" and
            (kinds.count("missing_column") != 1 or kinds.count("unexpected_column") != 1)):
            return "inadmissible"
    return None


def _parse_response(raw) -> DiagnoseOutput | None:
    valid = []
    for candidate in normalize_llm_json(raw):
        try:
            valid.append(DiagnoseOutput.model_validate(candidate))
        except ValidationError:
            continue
    return min(valid, key=lambda v: v.confidence) if valid else None


async def diagnose(state: dict) -> dict:
    findings = state.get("drift_items") or []
    date_findings = [f for f in findings if f.get("column") == "order_date" and
                     f.get("kind") in {"null_violation", "dtype_mismatch"}]
    if date_findings:
        # A date failure is never handed to the generic null/type LLM path.
        # The specialist reads the entire immutable source and must prove an
        # unambiguous declared format before any mapping can be proposed.
        proposal = await spec_date_parse({**state, "drift_items": date_findings})
        if not proposal.get("specialist_output", {}).get("date_parse"):
            return {**_DEFAULT_UNKNOWN, "diagnosis": "date format is malformed, ambiguous, or undeclared"}
        candidates = [{"drift_class": "date_parse", "finding_ids": [f["finding_id"] for f in date_findings],
                       "confidence": 0.95}]
        if len(date_findings) != len(findings):
            # Mixed date plus unrelated findings need independently complete
            # diagnosis; fail closed until such a batch is explicitly modeled.
            return {**_DEFAULT_UNKNOWN, "diagnosis": "mixed date and schema findings need review"}
        return {**_DEFAULT_UNKNOWN, "drift_class": "date_parse",
                "diagnosis": "complete unambiguous declared date format", "confidence": 0.95,
                "candidates": candidates}
    user_prompt = DIAGNOSE_USER_TEMPLATE.format(
        drift_items=json.dumps(state.get("drift_items") or [], indent=2),
        assertion_failures=json.dumps(state.get("assertion_failures") or [], indent=2),
        source_profile=json.dumps(state.get("source_profile") or {}, indent=2),
    )

    try:
        raw = await call_json(DIAGNOSE_SYSTEM, user_prompt)
    except LLMError as exc:
        log.warning("diagnose: LLM call failed, defaulting to unknown/low-confidence: %s", exc)
        return dict(_DEFAULT_UNKNOWN)

    parsed = _parse_response(raw)
    if parsed is None:
        log.warning("diagnose: no valid candidate in response, defaulting to unknown/low-confidence: raw=%r", raw)
        return dict(_DEFAULT_UNKNOWN)

    # When the model returns multiple candidates that disagree, trust the
    # most cautious one -- the same "when uncertain, prefer low confidence"
    # rule that governs every other judgement call in this design applies to
    # disagreement between the model's own candidates too.
    findings = state.get("drift_items") or []
    ids = {item["finding_id"] for item in findings}
    candidates = [c.model_dump() for c in parsed.candidates]
    initial_confidence = parsed.confidence
    if parsed.drift_class == "unknown":
        return {**_DEFAULT_UNKNOWN, "diagnosis": parsed.diagnosis}
    if not candidates and parsed.drift_class in {"rename", "type", "nullability", "value"}:
        candidates = [{"drift_class": parsed.drift_class, "confidence": parsed.confidence,
                       "finding_ids": list(ids)}]
    error = _candidate_error(candidates, findings)
    if error == "coverage" and findings and all(f["kind"] in {"missing_column", "unexpected_column",
                                                  "dtype_mismatch", "null_violation", "enum_value"}
                                   for f in findings):
        covered = {fid for c in candidates for fid in c["finding_ids"]}
        repair_prompt = DIAGNOSE_REPAIR_USER_TEMPLATE.format(
            drift_items=json.dumps(findings, indent=2),
            previous_response=json.dumps(parsed.model_dump(), indent=2),
            missing_ids=json.dumps(sorted(ids - covered)),
        )
        try:
            repair = _parse_response(await call_json(DIAGNOSE_SYSTEM, repair_prompt))
        except LLMError as exc:
            log.warning("diagnose: coverage repair failed: %s", exc)
            repair = None
        if repair is not None:
            if repair.drift_class == "unknown":
                return {**_DEFAULT_UNKNOWN, "diagnosis": repair.diagnosis}
            repaired_candidates = [c.model_dump() for c in repair.candidates]
            if _candidate_error(repaired_candidates, findings) is None:
                parsed = repair
                candidates = repaired_candidates
                for c in candidates:
                    c["confidence"] = min(c["confidence"], initial_confidence)
                error = None
    if error is not None:
        reason = ("diagnosis did not cover every finding" if error == "coverage"
                  else "diagnosis included an inadmissible finding")
        return {**_DEFAULT_UNKNOWN, "diagnosis": reason}
    return {
        "drift_class": parsed.drift_class,
        "diagnosis": parsed.diagnosis,
        "confidence": min(c["confidence"] for c in candidates),
        "candidates": candidates,
        "specialist_output": None,
        "specialist_outputs": None,
        "proposed_patch": None,
        "applied_patch": None,
        "components": [],
        "proposal_error": None,
        "patch_error": None,
    }
