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
from pydantic import BaseModel, ConfigDict, field_validator

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
