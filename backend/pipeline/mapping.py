"""The mapping schema — the only thing the healing agent is ever allowed to
write.

This module exists so that every write to the mapping, from anywhere (a
manual Phase 1 test, a human editing it later, or an LLM-proposed patch in
Phase 3), goes through the same Pydantic validation before it reaches
Postgres. A specialist "must not emit keys outside its section" — `extra =
"forbid"` enforces that at the model level; a specialist that hallucinates a
field the pipeline doesn't understand fails validation instead of silently
doing nothing.

Every value here is a *name* of a fixed operation, resolved against the
registries below — never a code string. The agent proposes config, never
Python.
"""

from typing import Callable

import pandas as pd
from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator

from pipeline.contract import load_contract

# ── Cast registry ────────────────────────────────────────────────────────────
# Each op takes the raw column and returns a coerced one. Unparseable values
# become NaN (via errors="coerce") rather than raising — a cast is allowed to
# not fully fix a column; that is what re-validation is for.


def _strip_commas_to_float(series: pd.Series) -> pd.Series:
    """'1,240.00' -> 1240.0. Handles already-numeric input too."""
    cleaned = series.astype(str).str.replace(",", "", regex=False)
    return pd.to_numeric(cleaned, errors="coerce")


CAST_REGISTRY: dict[str, Callable[[pd.Series], pd.Series]] = {
    "strip_commas_to_float": _strip_commas_to_float,
}


# ── Null-policy registry ─────────────────────────────────────────────────────
def _fill_unknown(series: pd.Series) -> pd.Series:
    return series.fillna("Unknown")


def _fill_zero(series: pd.Series) -> pd.Series:
    return series.fillna(0)


NULL_POLICY_REGISTRY: dict[str, Callable[[pd.Series], pd.Series]] = {
    "fill_unknown": _fill_unknown,
    "fill_zero": _fill_zero,
}


# Explicit alternate format, locale convention, and timezone are fixed by
# operation ID. The default strict ISO format stays valid in every version.
# The contract stores naive datetime64[ns]; values are interpreted in UTC.
DATE_PARSE_REGISTRY = {
    "dmy_slash_utc": {"format": "%d/%m/%Y", "locale": "day_first", "timezone": "UTC"},
}


class MappingPatch(BaseModel):
    """The full shape of `config/mapping.yaml` and of every patch applied to it.

    `extra="forbid"` is the enforcement of "a specialist must not emit keys
    outside its section" — an unrecognised top-level key fails validation
    rather than being silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    renames: dict[str, str] = {}
    casts: dict[str, str] = {}
    null_policy: dict[str, str] = {}
    drops: list[str] = []
    value_maps: dict[str, dict[str, str]] = {}
    date_parse: dict[str, str] = {}

    @field_validator("date_parse")
    @classmethod
    def _valid_date_parse(cls, value: dict[str, str], info: ValidationInfo) -> dict[str, str]:
        contract = (info.context or {}).get("contract") or load_contract()
        for column, operation in value.items():
            if contract.expected_dtypes.get(column) != "datetime64[ns]" or operation not in DATE_PARSE_REGISTRY:
                raise ValueError(f"unsupported date parse operation: {column}={operation}")
        return value

    @field_validator("value_maps")
    @classmethod
    def _valid_value_maps(cls, value_maps: dict[str, dict[str, str]], info: ValidationInfo) -> dict[str, dict[str, str]]:
        contract = (info.context or {}).get("contract") or load_contract()
        allowed = contract.allowed_values
        for column, replacements in value_maps.items():
            if column not in allowed or not replacements:
                raise ValueError(f"value map requires an allowed-value column: {column}")
            for source, target in replacements.items():
                if source in allowed[column] or target not in allowed[column] or source == target:
                    raise ValueError(f"invalid value map {column}: {source} -> {target}")
        return value_maps

    @field_validator("casts")
    @classmethod
    def _casts_are_known_ops(cls, v: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(v.values()) - set(CAST_REGISTRY))
        if unknown:
            raise ValueError(
                f"unknown cast op(s) {unknown}; "
                f"available: {sorted(CAST_REGISTRY)}"
            )
        return v

    @field_validator("null_policy")
    @classmethod
    def _null_policy_are_known_ops(cls, v: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(v.values()) - set(NULL_POLICY_REGISTRY))
        if unknown:
            raise ValueError(
                f"unknown null_policy op(s) {unknown}; "
                f"available: {sorted(NULL_POLICY_REGISTRY)}"
            )
        return v
