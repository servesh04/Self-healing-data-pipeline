"""Builds a Pandera schema from config/contract.yaml, and the `validate` step
that runs a DataFrame against it.

Pandera is the oracle: deterministic, not an LLM judging an LLM. Every
finding here is verified against pandera 0.32.1 / pandas 3.0.5, not assumed:

- A missing declared column raises a `column_in_dataframe` failure case.
- An undeclared column present in the frame (schema is `strict=True`) raises
  `column_in_schema`. Both carry the offending column name in `failure_case`,
  not in `column` (which is null for these two checks) — `_group_failures`
  below accounts for that.
- A dtype mismatch (e.g. revenue arriving as a string) raises one failure
  case per bad value, plus often a second, unrelated-looking failure from
  whichever check ran next (e.g. `greater_than_or_equal_to` erroring on a
  string/int comparison) — both are surfaced; a diagnosing reader (human now,
  LLM in Phase 3) needs the dtype failure to be in the list even if it isn't
  the only one.

`pandera.pandas` is the current import namespace for a pandas-backed schema,
not top-level `pandera` (ARCHITECTURE.md predates this).
"""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
import yaml
from pandera import Check, Column

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "config" / "contract.yaml"

# Contract dtype string -> the type pandera/pandas actually wants to compare
# against. "object" is kept as an alias to `str` for anyone hand-editing the
# YAML from ARCHITECTURE.md's original example; see the deviation note in
# contract.yaml and README.
_DTYPE_MAP: dict[str, object] = {
    "int64": int,
    "float64": float,
    "str": str,
    "object": str,
    "bool": bool,
    "datetime64[ns]": "datetime64[ns]",
}


@dataclass
class FailureCase:
    """One distinguishable, parseable validation failure."""

    column: str
    check: str
    failure_count: int
    example_values: list[str]

    def summary(self) -> str:
        examples = ", ".join(self.example_values[:3])
        return f"{self.column}: {self.check} ({self.failure_count} row(s), e.g. [{examples}])"


@dataclass
class ValidationResult:
    passed: bool
    failures: list[FailureCase] = field(default_factory=list)

    def as_strings(self) -> list[str]:
        return [f.summary() for f in self.failures]


@dataclass
class Contract:
    schema: pa.DataFrameSchema
    row_count_min: int
    expected_dtypes: dict[str, str]  # column -> declared dtype string, for diff_contract (Phase 3)


def _build_column(spec: dict) -> Column:
    dtype = _DTYPE_MAP[spec["dtype"]]
    checks = []
    if "allowed" in spec:
        checks.append(Check.isin(spec["allowed"]))
    if "min" in spec:
        checks.append(Check.ge(spec["min"]))
    return Column(
        dtype,
        nullable=spec.get("nullable", True),
        unique=spec.get("unique", False),
        checks=checks or None,
    )


@lru_cache
def load_contract(path: Path = CONTRACT_PATH) -> Contract:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    columns = {name: _build_column(spec) for name, spec in raw["columns"].items()}
    schema = pa.DataFrameSchema(columns, strict=True, coerce=False)
    return Contract(
        schema=schema,
        row_count_min=int(raw.get("row_count_min", 0)),
        expected_dtypes={name: spec["dtype"] for name, spec in raw["columns"].items()},
    )


def _group_failures(failure_cases: pd.DataFrame) -> list[FailureCase]:
    failures: list[FailureCase] = []
    for (column, check), group in failure_cases.groupby(
        ["column", "check"], dropna=False, sort=False
    ):
        examples = [str(v) for v in group["failure_case"].tolist()]
        # column_in_schema / column_in_dataframe: the real column name is in
        # failure_case, not in `column` (null for these structural checks).
        display_column = column if pd.notna(column) else "/".join(dict.fromkeys(examples))
        failures.append(
            FailureCase(
                column=str(display_column),
                check=str(check),
                failure_count=len(group),
                example_values=examples,
            )
        )
    return failures


def validate(df: pd.DataFrame, contract: Contract) -> ValidationResult:
    """Never raises. Pandera's own SchemaErrors is the one exception this
    function is allowed to catch — everything else is the caller's problem.
    """
    failures: list[FailureCase] = []

    try:
        contract.schema.validate(df, lazy=True)
    except pa.errors.SchemaErrors as exc:
        failures.extend(_group_failures(exc.failure_cases))

    if len(df) < contract.row_count_min:
        failures.append(
            FailureCase(
                column="__dataframe__",
                check="row_count_min",
                failure_count=1,
                example_values=[f"{len(df)} rows, need >= {contract.row_count_min}"],
            )
        )

    return ValidationResult(passed=not failures, failures=failures)
