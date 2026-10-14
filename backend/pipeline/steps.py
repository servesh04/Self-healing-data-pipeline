"""The transform steps. Deliberately dumb — see ARCHITECTURE.md, "The Pipeline
(keep it dumb)". No retries, no branching, no DAG engine. This exists only to
break in interesting ways.
"""

import pandas as pd

from pipeline.mapping import CAST_REGISTRY, NULL_POLICY_REGISTRY, DATE_PARSE_REGISTRY, MappingPatch


def parse_declared_date(series: pd.Series, operation: str) -> pd.Series:
    """Parse exact ISO or the declared alternate format, never inferred dates.

    Mapping versions persist across batches. Adding a DMY parser must not
    break subsequent batches that still use the pipeline's default ISO form.
    """
    if operation not in DATE_PARSE_REGISTRY:
        raise ValueError("unknown date parse operation")
    spec = DATE_PARSE_REGISTRY[operation]
    text = series.dropna().astype(str)
    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    if text.empty:
        return result
    iso = text.str.fullmatch(r"\d{4}-\d{2}-\d{2}")
    if operation == "dmy_slash_utc":
        alternate = text.str.fullmatch(r"\d{2}/\d{2}/\d{4}")
        if not (iso | alternate).all():
            raise ValueError("date format contains malformed values")
        if alternate.any():
            split = text[alternate].str.split("/", expand=True).astype(int)
            if ((split[0] <= 12) & (split[1] <= 12)).any():
                raise ValueError("ambiguous day/month date")
            result.loc[text[alternate].index] = pd.to_datetime(
                text[alternate], format=spec["format"], errors="coerce", exact=True)
    if iso.any():
        result.loc[text[iso].index] = pd.to_datetime(
            text[iso], format="%Y-%m-%d", errors="coerce", exact=True)
    if result.loc[text.index].isna().any():
        raise ValueError("date format contains invalid calendar dates")
    return result


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

    for column, op in mapping.date_parse.items():
        if column in df.columns:
            df[column] = parse_declared_date(df[column], op)

    for column, replacements in mapping.value_maps.items():
        if column in df.columns:
            df[column] = df[column].replace(replacements)

    return df


def transform(df: pd.DataFrame) -> pd.DataFrame:
    """Use ISO dates by default; declared alternate formats run in apply_mapping."""
    if "order_date" in df.columns and not pd.api.types.is_datetime64_any_dtype(df["order_date"]):
        # ISO is the sole default format. Slash dates cannot be inferred.
        df["order_date"] = pd.to_datetime(df["order_date"], format="%Y-%m-%d", errors="coerce", exact=True)
    return df
