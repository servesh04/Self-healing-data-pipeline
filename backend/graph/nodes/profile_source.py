"""STUB — Phase 2. Real column/dtype/null-count/sample-value profiling of the
source CSV arrives in Phase 3. Hardcoded so Phase 2 can prove the node
sequence without touching any file.
"""


def profile_source(state: dict) -> dict:
    return {
        "actual_schema": {"order_id": "int64", "region": "str", "revenue": "str"},
    }
