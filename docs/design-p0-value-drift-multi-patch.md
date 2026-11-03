# P0 Design Doc: Value Drift + Multi-Patch Proposals

**Status:** Superseded sketch. The authoritative P0 requirements and safety rules are in [expansion-review-and-implementation-plan.md](expansion-review-and-implementation-plan.md). In particular, day7 uses `N/S/E/W` mapped to contract labels `North/South/East/West`; diagnosis must cover every finding with per-component confidence; unknown findings block the whole batch; and UI, preflight, and compare-and-swap work are required. The proposals below that conflict with those rules are historical only.  
**Scope:** Two P0 features from the expansion proposal — (1A) Value Drift as a new healable drift class, and (2B) Multi-Patch Proposals to reduce heal cycles for combo drifts.

---

## Table of Contents

1. [Motivation & Goals](#1-motivation--goals)
2. [Feature 1: Value Drift](#2-feature-1-value-drift)
   - 2.1 [What It Detects](#21-what-it-detects)
   - 2.2 [Graph Changes](#22-graph-changes)
   - 2.3 [State Changes](#23-state-changes)
   - 2.4 [MappingPatch Extension](#24-mappingpatch-extension)
   - 2.5 [New Specialist: spec_value](#25-new-specialist-spec_value)
   - 2.6 [Pipeline Changes](#26-pipeline-changes)
   - 2.7 [Diagnose Prompt Changes](#27-diagnose-prompt-changes)
   - 2.8 [New Fault Dataset: day7_value_drift.csv](#28-new-fault-dataset-day7_value_driftcsv)
   - 2.9 [Routing Changes](#29-routing-changes)
   - 2.10 [Testing Plan](#210-testing-plan)
3. [Feature 2: Multi-Patch Proposals](#3-feature-2-multi-patch-proposals)
   - 3.1 [Problem Statement](#31-problem-statement)
   - 3.2 [Design Approach](#32-design-approach)
   - 3.3 [State Changes](#33-state-changes)
   - 3.4 [Node Changes](#34-node-changes)
   - 3.5 [Routing Changes](#35-routing-changes)
   - 3.6 [Human Approval Flow](#36-human-approval-flow)
   - 3.7 [Testing Plan](#37-testing-plan)
4. [Implementation Order](#4-implementation-order)
5. [Risks & Mitigations](#5-risks--mitigations)
6. [Files Touched Summary](#6-files-touched-summary)

---

## 1. Motivation & Goals

### Value Drift

The pipeline currently heals rename, type, and nullability drift. A common real-world drift is **value drift**: a categorical column's value set changes (e.g., `region` shifts from `[N,S,E,W]` to `[North,South,East,West]`). The contract's `isin()` check catches this as a `value_violation`, but `diff_contract` classifies all `value_violation` items the same way — and `diagnose` has no `"value"` drift class, so it routes to `escalate`. This is a healable drift that the pipeline currently can't fix.

**Goal:** Add value drift as a first-class healable drift class, symmetric with rename/type/nullability.

### Multi-Patch Proposals

When `day4_combo` (rename + type drift together) runs, the pipeline heals in 2 attempts: first the rename, then the type. Each cycle is: `diff_contract -> diagnose -> specialist -> propose_patch -> apply_patch -> rerun_validate`. The rename specialist can't propose a cast (scope-locked to `renames`), and the type specialist can't propose a rename. So combo drifts require N cycles for N independent drift items.

**Goal:** Allow `propose_patch` to receive multiple independent specialist outputs and merge them into a single patch, reducing combo drifts from N cycles to 1.

---

## 2. Feature 1: Value Drift

### 2.1 What It Detects

Value drift is when a categorical column's *set of valid values* changes, but the column name, type, and nullability are all correct. Example:

| Contract expects | Data actually has |
|---|---|
| `region` in `[N, S, E, W]` | `region` in `[North, South, East, West]` |

Pandera's `isin()` check fails, producing a `value_violation` DriftItem. Currently `diff_contract` maps `isin()` failures to `value_violation`, and `diagnose` sees a `value_violation` that isn't a formatting artifact → routes to `unknown` → escalate.

### 2.2 Graph Changes

No new graph nodes are needed. The existing topology already supports a new drift class:

```
diagnose -> route_after_diagnose -> spec_value -> propose_patch -> apply_patch -> rerun_validate
```

The only change is adding `"value"` to `route_after_diagnose`'s dispatch dict and adding the `spec_value` node to the graph.

### 2.3 State Changes

**`HealState.drift_class`** — add `"value"` to the Literal:

```python
# backend/graph/state.py
drift_class: Optional[Literal["rename", "type", "nullability", "value", "unknown"]]
```

That's the only state field change. `DriftItem.kind` already has `"value_violation"` as a valid kind. `HealAttempt` needs no changes — `specialist` is a string, `patch` is a dict.

### 2.4 MappingPatch Extension

Add a `value_maps` section to `MappingPatch`:

```python
# backend/pipeline/mapping.py
class MappingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    renames: dict[str, str] = {}
    casts: dict[str, str] = {}
    null_policy: dict[str, str] = {}
    drops: list[str] = []
    value_maps: dict[str, dict[str, str]] = {}  # NEW: {col: {old_val: new_val}}
```

The nested `dict[str, str]` maps actual values to expected values. Example:

```yaml
value_maps:
  region:
    North: N
    South: S
    East: E
    West: W
```

**Validation:** No registry needed for value maps — the keys/values are data-driven, not operation names. But we validate that the column exists in the contract:

```python
@field_validator("value_maps")
@classmethod
def _columns_exist(cls, v: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    from pipeline.contract import load_contract
    contract = load_contract()
    unknown_cols = sorted(set(v) - set(contract.expected_dtypes))
    if unknown_cols:
        raise ValueError(f"value_maps reference unknown column(s): {unknown_cols}")
    return v
```

### 2.5 New Specialist: spec_value

**File:** `backend/graph/nodes/spec_value.py`

Follows the exact same pattern as `spec_rename.py`:

```python
"""LLM specialist scoped to the `value_maps` section only.

Only ever reached when diagnose has classified the drift as "value".
Validates output against ValueSpecialistOutput (extra="forbid") before
anything downstream sees it.
"""

import json
import logging

from pydantic import ValidationError

from graph.schemas import ValueSpecialistOutput, normalize_llm_json
from prompts.prompts import SPEC_VALUE_SYSTEM, SPEC_VALUE_USER_TEMPLATE
from services.llm import LLMError, call_json

log = logging.getLogger(__name__)


async def spec_value(state: dict) -> dict:
    user_prompt = SPEC_VALUE_USER_TEMPLATE.format(
        drift_items=json.dumps(state.get("drift_items") or [], indent=2),
        source_profile=json.dumps(state.get("source_profile") or {}, indent=2),
        diagnosis=state.get("diagnosis") or "",
    )

    try:
        raw = await call_json(SPEC_VALUE_SYSTEM, user_prompt)
    except LLMError as exc:
        log.warning("spec_value: LLM call failed: %s", exc)
        return {"specialist_output": {}}

    for candidate in normalize_llm_json(raw):
        try:
            parsed = ValueSpecialistOutput.model_validate(candidate)
            return {"specialist_output": parsed.model_dump()}
        except ValidationError as exc:
            log.warning("spec_value: candidate failed validation: %s", exc)

    return {"specialist_output": {}}
```

**New schema** in `backend/graph/schemas.py`:

```python
class ValueSpecialistOutput(BaseModel):
    """Scoped to the `value_maps` section only."""

    model_config = ConfigDict(extra="forbid")

    value_maps: dict[str, dict[str, str]] = {}
```

Register in `SPECIALIST_SCHEMAS`:

```python
SPECIALIST_SCHEMAS: dict[str, type[BaseModel]] = {
    "rename": RenameSpecialistOutput,
    "type": TypeSpecialistOutput,
    "nullability": NullabilitySpecialistOutput,
    "value": ValueSpecialistOutput,  # NEW
}
```

**New prompts** in `backend/prompts/prompts.py`:

```python
SPEC_VALUE_SYSTEM = """You are the value-mapping specialist in a self-healing
data pipeline. You are only ever called when diagnose has already classified
the drift as "value" — your job is narrow: given that a categorical column's
values have changed, map each actual value to the expected value.

Respond with EXACTLY ONE JSON object matching this schema — never a list or
array of objects, never multiple candidate answers, just a single {...}:
{
  "value_maps": {
    "<column_name>": {
      "<actual_value>": "<expected_value>"
    }
  }
}

Rules:
- Emit ONLY the "value_maps" key. Never emit "renames", "casts", "null_policy",
  or "drops" — those belong to other specialists.
- The outer key is the column name; the inner keys are values as they actually
  appear in the data; the inner values are values the contract expects.
- Always propose your best mapping, even if the new values don't obviously
  resemble the old ones. Diagnose's confidence score — not you — decides
  whether this gets auto-applied or reviewed by a human first.
- Only return an empty value_maps object if the drift items given to you
  don't actually contain a value_violation at all."""

SPEC_VALUE_USER_TEMPLATE = """Drift items:
{drift_items}

Source data samples:
{source_profile}

Diagnosis: {diagnosis}

Map the values."""
```

### 2.6 Pipeline Changes

**`backend/pipeline/steps.py`** — add value map application in `apply_mapping`:

```python
def apply_mapping(df: pd.DataFrame, mapping: MappingPatch) -> pd.DataFrame:
    if mapping.renames:
        df = df.rename(columns=mapping.renames)

    if mapping.drops:
        df = df.drop(columns=[c for c in mapping.drops if c in df.columns])

    for column, op in mapping.casts.items():
        if column in df.columns:
            df[column] = CAST_REGISTRY[op](df[column])

    for column, op in mapping.null_policy.items():
        if column in df.columns:
            df[column] = NULL_POLICY_REGISTRY[op](df[column])

    # NEW: apply value maps after renames/casts/null_policy, before transform
    for column, value_map in mapping.value_maps.items():
        if column in df.columns:
            df[column] = df[column].map(lambda v: value_map.get(v, v))

    return df
```

The `.map(lambda v: value_map.get(v, v))` leaves unmapped values unchanged — if the specialist missed a value, it stays as-is and Pandera will catch it on rerun. This is the same "a patch is allowed to not fully fix a column" philosophy as casts.

### 2.7 Diagnose Prompt Changes

Add `"value"` as a drift class in `DIAGNOSE_SYSTEM`:

The current prompt says:
> `"drift_class": "rename" | "type" | "nullability" | "unknown"`

Change to:
> `"drift_class": "rename" | "type" | "nullability" | "value" | "unknown"`

Add a new bullet in QUESTION 1's classification rules:

```
- One or more value_violation items on a single column, where the actual
  values are recognizable variants of the expected values (e.g. "North" for
  "N", "1,240.00" for a float) — VALUE. This is a value-mapping-shaped drift:
  the column is right, the type is right, but the value set changed.
```

**Critical distinction from UNKNOWN:** The current prompt says value_violation with changed meaning → UNKNOWN. The new rule narrows this: if the actual values are *recognizable variants* of the expected values (same semantic domain, different representation), it's VALUE. If they're genuinely unrelated (e.g., product IDs where region codes should be), it's UNKNOWN. The diagnose LLM already has the example_values in its prompt to make this distinction.

### 2.8 New Fault Dataset: day7_value_drift.csv

**File:** `data/sources/day7_value_drift.csv`

Same 50-row structure as existing datasets, but `region` uses full words:

```csv
order_id,customer_id,region,order_date,revenue
1,101,North,2024-01-15,1240.50
2,102,South,2024-01-16,890.00
3,103,East,2024-01-17,2100.75
...
```

The contract expects `region` in `[N, S, E, W]`. This dataset should:
- Fail validation with a `value_violation` on `region`
- Be diagnosed as `drift_class: "value"` with high confidence
- Be healed by `spec_value` proposing `value_maps: {region: {North: N, South: S, East: E, West: W}}`
- Pass validation after the patch is applied

Add to `scripts/gen_fault_datasets.py` and `eval/run_eval.py`.

### 2.9 Routing Changes

**`backend/graph/routing.py`** — `route_after_diagnose`:

```python
def route_after_diagnose(state: HealState) -> str:
    if state["drift_class"] in ("rename", "type", "nullability", "value"):
        return f"spec_{state['drift_class']}"
    return "escalate"
```

No other routing changes needed. `route_after_confidence`, `route_after_rerun`, etc. are all class-agnostic.

### 2.10 Testing Plan

| Test | File | What it verifies |
|---|---|---|
| Value drift heals in 1 attempt | `test_pipeline.py` | day7 dataset passes after spec_value patch |
| `spec_value` scope lock | `test_graph_structure.py` | ValueSpecialistOutput rejects renames/casts keys |
| Routing for value class | `test_routing.py` | `route_after_diagnose` returns `spec_value` for `"value"` |
| MappingPatch value_maps validation | `test_pipeline.py` | Unknown column in value_maps fails validation |
| apply_mapping value_maps | `test_pipeline.py` | Values are correctly mapped in pipeline |
| Diagnose prompt mentions value | `test_regressions.py` | DIAGNOSE_SYSTEM contains "value" option |

---

## 3. Feature 2: Multi-Patch Proposals

### 3.1 Problem Statement

`day4_combo` has rename + type drift. Current flow:

```
Attempt 1: diagnose→rename → spec_rename proposes {renames: {cust_id: customer_id}}
           → apply → rerun → still fails (type drift remains)
Attempt 2: diagnose→type → spec_type proposes {casts: {revenue: strip_commas_to_float}}
           → apply → rerun → passes
```

The rename specialist can't propose a cast (scope-locked). The type specialist can't propose a rename. So combo drifts need N cycles for N independent drift items. Each cycle costs: 1 LLM call (diagnose) + 1 LLM call (specialist) + 1 LLM call (propose_patch) + 1 pipeline run + 1 DB write.

**Goal:** When diagnose identifies multiple independent drift classes in one pass, run multiple specialists and merge their patches into a single `MappingPatch`, applying them together in one `apply_patch` call.

### 3.2 Design Approach

The key insight: **diagnose already returns a single drift_class**. We're not changing diagnose. Instead, we change what happens *after* diagnose when the drift_items contain multiple independent drift shapes.

**Approach: Parallel specialist dispatch**

Instead of routing to a single specialist, `route_after_diagnose` can route to a new `multi_specialist` node that:
1. Reads all drift_items
2. Groups them by drift class (rename-shaped, type-shaped, nullability-shaped, value-shaped)
3. For each group, calls the appropriate specialist (can be parallel LLM calls)
4. Merges all specialist outputs into a single `MappingPatch`

This keeps the existing single-specialist path for simple drifts (day2, day3) and adds the multi-patch path for combo drifts (day4).

**Alternative considered:** Have diagnose return a list of drift classes. Rejected — it changes the diagnose contract, the HealState shape, and the prompt significantly. The current design (single class per diagnosis) is cleaner and the multi-patch logic belongs in the routing/specialist layer, not in diagnosis.

### 3.3 State Changes

**`HealState.proposed_patch`** — already `Optional[dict]`, no type change needed. A merged patch is still a dict.

**`HealState.specialist_output`** — currently `Optional[dict]`, holds a single specialist's output. For multi-patch, we need to hold multiple. Two options:

- **Option A:** Change to `Optional[list[dict]]` — but this breaks every node that reads it.
- **Option B:** Keep as `Optional[dict]` for single-specialist path, add new `specialist_outputs: Optional[list[dict]]` for multi-patch path. `propose_patch` checks both.

**Chosen: Option B** — additive, doesn't break existing single-specialist flow.

```python
# backend/graph/state.py — add to HealState
specialist_outputs: Optional[list[dict]]  # multi-patch: one per specialist
```

**`HealAttempt.patch`** — already `dict`, a merged patch is still a dict. No change.

**`HealAttempt.specialist`** — currently a string like `"spec_rename"`. For multi-patch, change to a comma-separated string: `"spec_rename+spec_type"`. This is display-only; the frontend already renders it as a string.

### 3.4 Node Changes

#### 3.4.1 New node: `multi_specialist`

**File:** `backend/graph/nodes/multi_specialist.py`

```python
"""Runs multiple specialists in parallel and merges their outputs.

Only called when route_after_diagnose detects multiple independent drift
shapes. Groups drift_items by class, dispatches to the appropriate
specialist for each group, and merges all outputs into a single patch.

Follows the same never-raises guardrail as every other node: if any
specialist fails, its section is simply empty in the merged output.
"""

import json
import logging

from graph.nodes.spec_nullability import spec_nullability
from graph.nodes.spec_rename import spec_rename
from graph.nodes.spec_type import spec_type
from graph.nodes.spec_value import spec_value
from graph.state import HealState
from services.llm import LLMError, call_json

log = logging.getLogger(__name__)

# Maps drift class -> specialist node function
SPECIALIST_NODES = {
    "rename": spec_rename,
    "type": spec_type,
    "nullability": spec_nullability,
    "value": spec_value,
}


def _classify_drift_items(drift_items: list[dict]) -> dict[str, list[dict]]:
    """Group drift_items by their implied drift class."""
    groups: dict[str, list[dict]] = {
        "rename": [],
        "type": [],
        "nullability": [],
        "value": [],
    }
    for item in drift_items:
        kind = item.get("kind", "")
        if kind in ("missing_column", "unexpected_column"):
            groups["rename"].append(item)
        elif kind == "dtype_mismatch":
            groups["type"].append(item)
        elif kind == "null_violation":
            groups["nullability"].append(item)
        elif kind == "value_violation":
            groups["value"].append(item)
    return groups


async def multi_specialist(state: dict) -> dict:
    drift_items = state.get("drift_items") or []
    groups = _classify_drift_items(drift_items)

    # Only run specialists that have drift items to work with
    active_classes = [cls for cls, items in groups.items() if items]

    if len(active_classes) <= 1:
        # Single drift class — shouldn't be here, but handle gracefully
        # by falling back to the single specialist
        if not active_classes:
            return {"specialist_output": {}, "specialist_outputs": []}
        cls = active_classes[0]
        specialist_fn = SPECIALIST_NODES[cls]
        result = await specialist_fn(state)
        return {
            "specialist_output": result.get("specialist_output", {}),
            "specialist_outputs": [result.get("specialist_output", {})],
        }

    # Run all active specialists (sequentially for now — parallel LLM calls
    # can hit rate limits; sequential is safer and still correct)
    outputs = []
    for cls in active_classes:
        specialist_fn = SPECIALIST_NODES[cls]
        try:
            result = await specialist_fn(state)
            output = result.get("specialist_output", {})
            if output:  # only include non-empty outputs
                outputs.append(output)
        except Exception as exc:
            log.warning("multi_specialist: %s failed: %s", cls, exc)

    # Merge all outputs into a single patch dict
    merged: dict = {}
    for output in outputs:
        merged.update(output)

    return {
        "specialist_output": merged,
        "specialist_outputs": outputs,
    }
```

#### 3.4.2 Modify `propose_patch`

Change to handle both single and multi-patch:

```python
async def propose_patch(state: dict) -> dict:
    # Prefer multi-patch output if available
    patch = state.get("specialist_outputs")
    if patch:
        # Multi-patch: merge all specialist outputs into one dict
        merged: dict = {}
        for output in patch:
            merged.update(output)
        patch = merged
    else:
        patch = state.get("specialist_output") or {}

    # ... rest unchanged ...
```

#### 3.4.3 Modify `apply_patch`

No changes needed. `apply_patch` already merges the patch into the current mapping section by section:

```python
merged = MappingPatch(
    renames={**current.renames, **patch.get("renames", {})},
    casts={**current.casts, **patch.get("casts", {})},
    null_policy={**current.null_policy, **patch.get("null_policy", {})},
    drops=list(dict.fromkeys([*current.drops, *patch.get("drops", [])])),
    value_maps={**current.value_maps, **patch.get("value_maps", {})},  # NEW
)
```

A multi-patch `{"renames": {...}, "casts": {...}}` merges correctly — renames go to renames, casts go to casts.

### 3.5 Routing Changes

**`route_after_diagnose`** — detect when multiple independent drift shapes are present:

```python
def route_after_diagnose(state: HealState) -> str:
    drift_items = state.get("drift_items") or []

    # Count distinct drift classes present in the items
    classes_present = set()
    for item in drift_items:
        kind = item.get("kind", "")
        if kind in ("missing_column", "unexpected_column"):
            classes_present.add("rename")
        elif kind == "dtype_mismatch":
            classes_present.add("type")
        elif kind == "null_violation":
            classes_present.add("nullability")
        elif kind == "value_violation":
            classes_present.add("value")

    # Multiple independent drift classes -> multi-patch path
    if len(classes_present) > 1:
        return "multi_specialist"

    # Single drift class -> existing single-specialist path
    if state["drift_class"] in ("rename", "type", "nullability", "value"):
        return f"spec_{state['drift_class']}"

    return "escalate"
```

**New edge in `build.py`:**

```python
g.add_node("multi_specialist", multi_specialist)
g.add_edge("multi_specialist", "propose_patch")
```

Add `"multi_specialist"` to the conditional edges from `diagnose`.

### 3.6 Human Approval Flow

When `propose_patch` returns a multi-patch and confidence < 0.75, the existing `human_approval` node handles it. The `ApprovalPanel` component already renders the patch as a JSON diff — a multi-patch will show multiple sections changing. No frontend changes needed for the approval flow.

The `PatchDiff` component already renders all sections of a patch. A multi-patch with both `renames` and `casts` will show both sections. No changes needed.

### 3.7 Testing Plan

| Test | File | What it verifies |
|---|---|---|
| day4_combo heals in 1 attempt | `test_pipeline.py` | Multi-patch applies rename+type together |
| multi_specialist grouping | `test_graph_structure.py` | `_classify_drift_items` correctly groups mixed drift |
| Routing for combo drift | `test_routing.py` | `route_after_diagnose` returns `multi_specialist` for mixed items |
| apply_patch merges multi-patch | `test_pipeline.py` | Both renames and casts applied in one patch |
| specialist_outputs in state | `test_serialization.py` | Multi-patch state serializes correctly |
| Propose_patch merges outputs | `test_graph_structure.py` | Multiple specialist outputs merged into one patch |

---

## 4. Implementation Order

| Step | Feature | Files | Depends On |
|---|---|---|---|
| 1 | Value drift: MappingPatch + steps | `mapping.py`, `steps.py` | — |
| 2 | Value drift: schema + specialist | `schemas.py`, `spec_value.py` | Step 1 |
| 3 | Value drift: prompts + routing | `prompts.py`, `routing.py` | Step 2 |
| 4 | Value drift: graph wiring | `build.py` | Step 3 |
| 5 | Value drift: fault dataset + eval | `day7_value_drift.csv`, `gen_fault_datasets.py`, `run_eval.py` | Step 4 |
| 6 | Multi-patch: multi_specialist node | `multi_specialist.py` | — |
| 7 | Multi-patch: propose_patch + routing | `propose_patch.py`, `routing.py` | Step 6 |
| 8 | Multi-patch: graph wiring | `build.py` | Step 7 |
| 9 | Tests: value drift | `test_pipeline.py`, `test_routing.py`, etc. | Step 5 |
| 10 | Tests: multi-patch | `test_pipeline.py`, `test_routing.py`, etc. | Step 8 |
| 11 | Eval: all 7 datasets | `run_eval.py`, `eval_results.json` | Steps 9, 10 |

---

## 5. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Diagnose misclassifies value drift as unknown | day7 escalates instead of healing | Prompt explicitly defines value vs. unknown distinction; eval verifies |
| Multi-patch produces overlapping specialists | Two specialists propose conflicting patches | `_classify_drift_items` ensures each drift item goes to exactly one specialist group |
| Multi-patch LLM calls exceed rate limits | Specialists fail silently, partial patch | Sequential execution (not parallel); empty sections are valid |
| MappingPatch value_maps validator loads contract on every validation | Performance | Contract is cached by `load_contract()` (already the case for casts/null_policy) |
| Routing change breaks existing single-drift cases | day2/day3 regressions | `route_after_diagnose` only returns `multi_specialist` when `len(classes_present) > 1` |
| Frontend can't render value_maps section | Dashboard shows empty patch | PatchDiff already iterates all sections; value_maps renders as nested dict |

---

## 6. Files Touched Summary

| File | Change |
|---|---|
| `backend/graph/state.py` | Add `"value"` to drift_class Literal; add `specialist_outputs` field |
| `backend/graph/routing.py` | Add `"value"` to route_after_diagnose; add multi-patch detection |
| `backend/graph/build.py` | Add `spec_value` and `multi_specialist` nodes + edges |
| `backend/graph/schemas.py` | Add `ValueSpecialistOutput`; register in `SPECIALIST_SCHEMAS` |
| `backend/pipeline/mapping.py` | Add `value_maps` section to `MappingPatch` |
| `backend/pipeline/steps.py` | Apply value_maps in `apply_mapping` |
| `backend/prompts/prompts.py` | Add `SPEC_VALUE_SYSTEM`, `SPEC_VALUE_USER_TEMPLATE`; update `DIAGNOSE_SYSTEM` |
| `backend/graph/nodes/spec_value.py` | **NEW** — value specialist node |
| `backend/graph/nodes/multi_specialist.py` | **NEW** — multi-specialist dispatch/merge node |
| `backend/graph/nodes/propose_patch.py` | Handle `specialist_outputs` (multi-patch) |
| `backend/graph/nodes/diff_contract.py` | No changes (value_violation already mapped) |
| `data/sources/day7_value_drift.csv` | **NEW** — value drift fault dataset |
| `scripts/gen_fault_datasets.py` | Add day7 generation |
| `eval/run_eval.py` | Add day7 to eval sweep |
| `tests/test_pipeline.py` | Value drift + multi-patch pipeline tests |
| `tests/test_routing.py` | Value class + multi-patch routing tests |
| `tests/test_graph_structure.py` | Multi-specialist grouping, schema tests |
| `tests/test_serialization.py` | Multi-patch state serialization |

**Estimated new code:** ~350 lines (nodes, schemas, prompts, routing)  
**Estimated new tests:** ~15 tests  
**Estimated new data:** 1 CSV (50 rows)
