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
from pathlib import Path
import hashlib
import json
import re

import pandas as pd
import pandera.pandas as pa
import yaml
from pandera import Check, Column

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
CONTRACT_PATH = CONFIG_DIR / "contracts" / "default.yaml"
LEGACY_CONTRACT_PATH = CONFIG_DIR / "contract.yaml"
CONTRACTS_DIR = CONFIG_DIR / "contracts"
_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")

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
    allowed_values: dict[str, list[str]] = field(default_factory=dict)
    name: str = "default"
    version: str = ""
    spec: dict = field(default_factory=dict)


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError(f"duplicate contract key: {key}")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _validated_spec(raw: dict) -> dict:
    if not isinstance(raw, dict) or set(raw) - {"columns", "row_count_min"}:
        raise ValueError("contract has invalid top-level keys")
    columns = raw.get("columns")
    if not isinstance(columns, dict) or not columns:
        raise ValueError("contract requires nonempty columns")
    if not isinstance(raw.get("row_count_min", 0), int) or raw.get("row_count_min", 0) < 0:
        raise ValueError("row_count_min must be a nonnegative integer")
    for name, spec in columns.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError(f"invalid contract column: {name}")
        if not isinstance(spec, dict) or set(spec) - {"dtype", "nullable", "unique", "allowed", "min"}:
            raise ValueError(f"invalid contract column spec: {name}")
        if spec.get("dtype") not in _DTYPE_MAP:
            raise ValueError(f"unknown dtype for {name}")
        if any(key in spec and not isinstance(spec[key], bool) for key in ("nullable", "unique")):
            raise ValueError(f"nullable/unique must be boolean: {name}")
        if "allowed" in spec:
            allowed = spec["allowed"]
            if spec["dtype"] not in ("str", "object") or not isinstance(allowed, list) or not allowed or not all(isinstance(v, str) for v in allowed) or len(set(allowed)) != len(allowed):
                raise ValueError(f"invalid allowed values: {name}")
        if "min" in spec and (not isinstance(spec["min"], (int, float)) or isinstance(spec["min"], bool)):
            raise ValueError(f"invalid minimum: {name}")
    return raw


def contract_version(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def contract_from_snapshot(spec: dict, name: str = "default", version: str | None = None) -> Contract:
    spec = _validated_spec(spec)
    actual_version = contract_version(spec)
    if version is not None and version != actual_version:
        raise ValueError("pinned contract snapshot hash mismatch")
    columns = {column: _build_column(column_spec) for column, column_spec in spec["columns"].items()}
    return Contract(
        schema=pa.DataFrameSchema(columns, strict=True, coerce=False),
        row_count_min=int(spec.get("row_count_min", 0)),
        expected_dtypes={column: column_spec["dtype"] for column, column_spec in spec["columns"].items()},
        allowed_values={column: list(column_spec["allowed"]) for column, column_spec in spec["columns"].items() if "allowed" in column_spec},
        name=name, version=actual_version, spec=spec,
    )


# ── Uploaded-contract overlay ───────────────────────────────────────────────
# File contracts under config/contracts/ remain the immutable, authoritative
# registry: they are read from disk and an upload can never take their name
# (services.contract_store rejects a colliding registration). Contracts the
# user registers through the app live in Postgres and are materialised here
# once per process by contract_store.load_into_registry(). Keeping the overlay
# module-level is what lets every existing caller -- routers/runs.py,
# routers/config.py, services/store.py, contract_for_state -- resolve an
# uploaded name without changing any of their signatures.
#
# Deliberately a plain dict, replaced wholesale rather than mutated row by row:
# a partially-loaded registry would let a run start against a contract that
# does not exist in the database.
_uploaded: dict[str, Contract] = {}


def set_uploaded_contracts(contracts: dict[str, Contract]) -> None:
    """Atomically replace the uploaded-contract overlay, validating every entry."""
    global _uploaded
    checked: dict[str, Contract] = {}
    for name, contract in contracts.items():
        if not _NAME.fullmatch(name) or contract.name != name:
            raise ValueError(f"invalid uploaded contract name: {name}")
        checked[name] = contract
    _uploaded = checked


def clear_uploaded_contracts() -> None:
    global _uploaded
    _uploaded = {}


def uploaded_contracts() -> dict[str, Contract]:
    return dict(_uploaded)


class ContractRegistry:
    def __init__(self, directory: Path = CONTRACTS_DIR):
        self.directory = Path(directory)
        self._paths: dict[str, Path] = {}
        for path in sorted(self.directory.glob("*.yaml")):
            name = path.stem.lower()
            if not _NAME.fullmatch(name) or name in self._paths:
                raise ValueError(f"duplicate or invalid contract name: {path.stem}")
            self._paths[name] = path
        if "default" not in self._paths:
            raise ValueError("default contract is missing")
        for name, path in self._paths.items():
            load_contract(path, name)

    def names(self) -> list[str]:
        return sorted(set(self._paths) | set(_uploaded))

    def get(self, name: str) -> Contract:
        if name in self._paths:
            return load_contract(self._paths[name], name)
        if name in _uploaded:
            return _uploaded[name]
        raise ValueError(f"unknown contract: {name}")


def get_registry() -> ContractRegistry:
    return ContractRegistry()


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


def load_contract(path: Path = CONTRACT_PATH, name: str = "default") -> Contract:
    path = Path(path)
    if path == LEGACY_CONTRACT_PATH:
        path = CONTRACT_PATH
    raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    return contract_from_snapshot(raw, name)


def contract_for_state(state: dict) -> Contract:
    """Use the checkpointed spec on resume, even if registry files changed."""
    spec = state.get("contract_snapshot")
    if spec is not None:
        return contract_from_snapshot(spec, state.get("contract_name", "default"), state.get("contract_version"))
    return get_registry().get(state.get("contract_name", "default"))


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
