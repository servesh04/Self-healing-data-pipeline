"""STUB — Phase 2. Real deterministic diff against config/contract.yaml
arrives in Phase 3.

Runs on every heal-cycle loop-back (see routing.route_after_rerun), not just
once — topology-faithful to ARCHITECTURE.md's stated cycle
(`rerun_validate → diff_contract → diagnose → ...`), even though this stub's
output does not change between iterations. What the *real* Phase 3 version
should recompute on each loop-back — the full raw-file diff again, or just
the remaining assertion failures — is a design question for that phase, not
this one; this stub only needs to exist as a node and be revisited.
"""

from graph.state import DriftItem


def diff_contract(state: dict) -> dict:
    drift: DriftItem = {
        "kind": "dtype_mismatch",
        "column": "revenue",
        "expected": "float64",
        "actual": "str",
        "example_values": ["1,240.00"],
    }
    return {
        "expected_schema": {
            "order_id": "int64",
            "region": "str",
            "revenue": "float64",
        },
        "drift_items": [drift],
    }
