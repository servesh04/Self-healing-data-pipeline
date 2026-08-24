"""HealState — the graph's single state object, exactly as specified in
ARCHITECTURE.md. An evaluator reads this file first alongside routing.py:
every field here is either set by a node or read by a router, nothing is
decorative.

`history` is the one field that must NOT behave like the others: every other
field overwrites on each node's return (LangGraph's default), but history
must APPEND — `Annotated[list[HealAttempt], operator.add]` is what makes that
happen. Verified in Phase 0 against langgraph 1.2.11 by actually running a
two-attempt cycle and checking the accumulated list, not by reading the docs.
"""

import operator
from typing import Annotated, Literal, Optional, TypedDict


class DriftItem(TypedDict):
    kind: Literal[
        "missing_column",
        "unexpected_column",
        "dtype_mismatch",
        "null_violation",
        "value_violation",
    ]
    column: str
    expected: str
    actual: str
    example_values: list[str]


class HealAttempt(TypedDict):
    attempt_no: int
    drift_summary: str
    specialist: str
    patch: dict
    confidence: float
    approved_by: Optional[str]  # "auto" | "human" | None
    result: Literal["passed", "failed", "rejected"]
    assertion_failures: list[str]


class HealState(TypedDict):
    # ── Inputs ──
    run_id: str
    source_path: str

    # ── Pipeline result ──
    assertions_passed: bool
    assertion_failures: list[str]
    rows_in: int
    rows_out: int

    # ── Profile + diff (deterministic) ──
    actual_schema: dict[str, str]
    expected_schema: dict[str, str]
    drift_items: list[DriftItem]

    # ── Diagnosis (LLM in Phase 3; hardcoded in Phase 2) ──
    drift_class: Optional[Literal["rename", "type", "nullability", "unknown"]]
    diagnosis: Optional[str]
    confidence: Optional[float]  # 0.0 – 1.0

    # ── Patch ──
    proposed_patch: Optional[dict]
    patch_rationale: Optional[str]
    applied_patch: Optional[dict]

    # ── Human gate ──
    awaiting_approval: bool
    human_decision: Optional[Literal["approve", "reject"]]
    human_note: Optional[str]

    # ── Guardrail counter — REQUIRED ──
    heal_attempts: int

    # ── Terminal ──
    status: Literal["running", "healed", "escalated", "clean", "awaiting_approval"]
    final_message: Optional[str]

    # ── Audit trail — append-only, drives the UI ──
    history: Annotated[list[HealAttempt], operator.add]


MAX_HEAL_ATTEMPTS = 3
CONFIDENCE_THRESHOLD = 0.75
RECURSION_LIMIT = 30  # pass as {"recursion_limit": RECURSION_LIMIT} on invoke/stream
