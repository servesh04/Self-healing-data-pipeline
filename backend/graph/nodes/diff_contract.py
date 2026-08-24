"""Real node — Phase 3, deterministic. Compares actual schema against
config/contract.yaml and emits a structured drift_items list. "This is a
diff, not a judgement" (ARCHITECTURE.md's node spec).

Runs on every heal-cycle loop-back (routing.route_after_rerun loops here, not
back to profile_source), and its output MUST change between iterations —
verified against real day4_combo data before writing this: with an empty
mapping, Pandera reports 4 failure signals (2 for the rename, 2 for the type
drift); after a rename-only patch, only the 2 type-drift signals remain, a
strict subset. If diff_contract instead re-derived drift_items from
profile_source's one-time raw-file snapshot, it would keep reporting the
already-fixed rename drift every iteration, and diagnose would have no signal
that anything changed — which is exactly how an agent ends up re-proposing
overlapping or redundant patches and never converging in the expected number
of attempts.

So diff_contract reads `assertion_failure_details` — the structured form of
whatever run_pipeline or rerun_validate (whichever ran most recently) just
observed from a REAL pipeline run — and translates each Pandera failure
signature into a DriftItem. It does not re-run the pipeline itself; that
would duplicate work the node immediately before it in the same iteration
already did.
"""

from graph.state import DriftItem
from pipeline.contract import load_contract

_CHECK_KIND_MAP = {
    "column_in_dataframe": "missing_column",
    "column_in_schema": "unexpected_column",
    "not_nullable": "null_violation",
    "field_uniqueness": "value_violation",
}


def _kind_for(check: str) -> str:
    if check in _CHECK_KIND_MAP:
        return _CHECK_KIND_MAP[check]
    if check.startswith("dtype("):
        return "dtype_mismatch"
    if check.startswith("isin("):
        return "value_violation"
    return "value_violation"  # fallback for e.g. greater_than_or_equal_to(...)


def diff_contract(state: dict) -> dict:
    contract = load_contract()
    details = state.get("assertion_failure_details") or []

    drift_items: list[DriftItem] = []
    for d in details:
        kind = _kind_for(d["check"])
        # d["column"] is already resolved by pipeline/contract.py's
        # _group_failures for the structural checks (column_in_schema /
        # column_in_dataframe), which pandera itself reports with column=None
        # and the real name only in failure_case — no need to re-derive it
        # here, and doing so from example_values[0] would silently drop
        # information if two unexpected columns ever share one failure group.
        column = d["column"]
        expected = (
            "(not declared in contract)"
            if kind == "unexpected_column"
            else contract.expected_dtypes.get(column, d["check"])
        )

        drift_items.append(
            {
                "kind": kind,
                "column": column,
                "expected": str(expected),
                "actual": d["check"],
                "example_values": [str(v) for v in d["example_values"][:5]],
            }
        )

    return {
        "expected_schema": contract.expected_dtypes,
        "drift_items": drift_items,
    }
