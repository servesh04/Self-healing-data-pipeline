"""The transform steps. Deliberately dumb — see ARCHITECTURE.md, "The Pipeline
(keep it dumb)". No retries, no branching, no DAG engine. This exists only to
break in interesting ways.
"""

import pandas as pd

from pipeline.mapping import CAST_REGISTRY, NULL_POLICY_REGISTRY, MappingPatch


def apply_mapping(df: pd.DataFrame, mapping: MappingPatch) -> pd.DataFrame:
    """Apply the current mapping: renames, drops, casts, null policy — in
    that order, since a cast or null-fill needs to address the column by its
    *renamed* name, and a dropped column should not be cast or filled.

    Silently no-ops on a column the mapping names but the frame doesn't have;
    that is a drift signal for diff_contract (Phase 3) to surface, not a
    reason for this dumb step to fail.
    """
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

    return df


def transform(df: pd.DataFrame) -> pd.DataFrame:
    """Fixed, non-mapping-driven shaping. Not something the agent patches —
    date parsing is not one of the drift classes this project heals.

    Unparseable dates become NaT (errors="coerce"), which the contract's
    `nullable: false` on order_date will then flag as a validation failure
    rather than raising here.
    """
    if "order_date" in df.columns:
        df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    return df
