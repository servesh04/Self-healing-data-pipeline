"""Postgres-backed storage for contracts uploaded through the app.

The YAML files in config/contracts/ stay the immutable, authoritative
registry. This module adds a *second* source of contracts -- rows in
`app_contracts` -- without touching the files or the run history:

* `app_contracts`         one row per registered name: the current spec,
                          its `contract_version()` hash and provenance.
* `app_contract_versions` append-only spec history for that name, keyed by
                          (name, version).

Uploaded bytes are parsed in memory. Nothing here writes a file, imports
uploaded content, evaluates it, or accepts a path. The only write targets are
these two tables.

Validation deliberately delegates to pipeline.contract: `_validated_spec`
owns the contract grammar, `contract_from_snapshot` proves Pandera can build
the schema, and `contract_version` owns the hash. A second validator would
drift from the one the pipeline actually uses.
"""

from dataclasses import dataclass
import json
import math

import psycopg
from psycopg.types.json import Json
import yaml

from db import get_pool
from pipeline.contract import (
    CONTRACTS_DIR,
    Contract,
    _NAME,
    _UniqueKeyLoader,
    _validated_spec,
    contract_from_snapshot,
    get_registry,
    set_uploaded_contracts,
)

MAX_CONTRACT_BYTES = 256 * 1024
MAX_CONTRACT_COLUMNS = 200
MAX_NAME_LENGTH = 64


class ContractUploadError(ValueError):
    """Anything the client can fix: malformed spec, bad name, bad constraints."""


class ContractTooLarge(ContractUploadError):
    pass


class ContractConflict(ContractUploadError):
    """The requested name is already taken by a file or uploaded contract."""


class ContractImmutable(ContractUploadError):
    """File-based contracts (including `default`) can never be deleted."""


class ContractNotFound(ContractUploadError):
    pass


@dataclass(frozen=True)
class ParsedContract:
    name: str | None
    spec: dict
    version: str
    format: str

    @property
    def columns(self) -> list[str]:
        return list(self.spec["columns"])

    @property
    def column_count(self) -> int:
        return len(self.spec["columns"])


# ── Parsing / validation (pure, no DB) ──────────────────────────────────────

def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate contract key: {key}")
        result[key] = value
    return result


def _parse_document(text: str) -> tuple[dict, str]:
    """Return (document, format). Content decides, not the client's filename."""
    try:
        return json.loads(text, object_pairs_hook=_no_duplicate_keys), "json"
    except json.JSONDecodeError:
        # Not JSON -- fall through to YAML, which is a superset of it. This is
        # also how a .json file containing YAML still gets a useful parse.
        pass
    except ValueError as exc:  # duplicate key from the pairs hook
        raise ContractUploadError(f"malformed contract document: {exc}") from exc
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader), "yaml"
    except yaml.YAMLError as exc:
        raise ContractUploadError(f"malformed contract document: {exc}") from exc


def _check_limits(spec: dict) -> None:
    if len(spec["columns"]) > MAX_CONTRACT_COLUMNS:
        raise ContractUploadError(
            f"contract has too many columns (max {MAX_CONTRACT_COLUMNS})"
        )
    for column, column_spec in spec["columns"].items():
        if "min" in column_spec:
            try:
                finite = math.isfinite(float(column_spec["min"]))
            except (OverflowError, ValueError):
                finite = False
            if not finite:
                # _validated_spec accepts any int/float, including NaN/Infinity
                # (and an int too large for float), which would make Pandera's
                # Check.ge behave unpredictably.
                raise ContractUploadError(f"invalid minimum: {column}")


def validate_name(name: str | None) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ContractUploadError("contract name is required")
    candidate = name.strip()
    if len(candidate) > MAX_NAME_LENGTH or not _NAME.fullmatch(candidate):
        raise ContractUploadError(
            "contract name must match ^[a-z][a-z0-9_-]*$ and be at most "
            f"{MAX_NAME_LENGTH} characters"
        )
    return candidate


def parse_contract_upload(payload: bytes, name: str | None = None) -> ParsedContract:
    """Parse and validate an uploaded document without persisting anything."""
    if not payload:
        raise ContractUploadError("contract upload is empty")
    if len(payload) > MAX_CONTRACT_BYTES:
        raise ContractTooLarge(
            f"contract upload exceeds {MAX_CONTRACT_BYTES // 1024} KiB"
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractUploadError("contract upload must be UTF-8 encoded") from exc

    document, document_format = _parse_document(text)
    try:
        spec = _validated_spec(document)
    except ValueError as exc:
        raise ContractUploadError(str(exc)) from exc
    _check_limits(spec)
    # Building the real Pandera schema here is the strongest validation we
    # have: it fails a spec _validated_spec would let through but Pandera
    # cannot actually construct.
    contract = contract_from_snapshot(spec, name or "preview")
    return ParsedContract(
        name=validate_name(name) if name is not None else None,
        spec=spec,
        version=contract.version,
        format=document_format,
    )


def column_preview(spec: dict) -> list[dict]:
    """One display row per column, defaulting exactly like _build_column()."""
    return [
        {
            "name": column,
            "dtype": column_spec["dtype"],
            "nullable": column_spec.get("nullable", True),
            "unique": column_spec.get("unique", False),
            "allowed": list(column_spec["allowed"]) if "allowed" in column_spec else None,
            "min": column_spec.get("min"),
        }
        for column, column_spec in spec["columns"].items()
    ]


def file_contract_names() -> set[str]:
    return {path.stem.lower() for path in CONTRACTS_DIR.glob("*.yaml")}


def name_available(name: str) -> bool:
    validate_name(name)
    return name not in get_registry().names()


# ── Schema ──────────────────────────────────────────────────────────────────

async def ensure_schema() -> None:
    """Idempotent. Owner-role only in production; also run at demo startup."""
    async with get_pool().connection() as conn:
        await conn.execute(
            """
            create table if not exists app_contracts (
                name       text        primary key,
                spec       jsonb       not null,
                version    text        not null,
                source     text        not null default 'upload',
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now(),
                constraint app_contracts_name_format
                    check (name ~ '^[a-z][a-z0-9_-]*$'),
                constraint app_contracts_source check (source in ('upload'))
            )
            """
        )
        await conn.execute(
            """
            create table if not exists app_contract_versions (
                name       text        not null
                    references app_contracts(name) on delete cascade,
                version    text        not null,
                spec       jsonb       not null,
                source     text        not null default 'upload',
                created_at timestamptz not null default now(),
                primary key (name, version)
            )
            """
        )
        await conn.execute(
            "create index if not exists idx_app_contract_versions_name_created "
            "on app_contract_versions (name, created_at desc)"
        )


# ── Reads ───────────────────────────────────────────────────────────────────

def _summary_row(row: dict, contract: Contract) -> dict:
    return {
        "name": row["name"],
        "version": row["version"],
        "source": row.get("source", "upload"),
        "deletable": True,
        "column_count": len(contract.expected_dtypes),
        "columns": list(contract.expected_dtypes),
        "created_at": row.get("created_at"),
    }


def _version_rows(rows: list[dict], current: str) -> list[dict]:
    return [
        {
            "version": row["version"],
            "source": row.get("source", "upload"),
            "column_count": len(row["spec"].get("columns", {})),
            "current": row["version"] == current,
            "created_at": row.get("created_at"),
        }
        for row in rows
    ]


async def list_contracts() -> list[dict]:
    """Uploaded contracts only; the router merges in file contracts."""
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "select name, version, spec, source, created_at, updated_at "
            "from app_contracts order by name"
        )
        rows = list(await cur.fetchall())
    summaries = []
    for row in rows:
        contract = contract_from_snapshot(row["spec"], row["name"], row["version"])
        summaries.append(_summary_row(row, contract))
    return summaries


async def get_contract(name: str) -> dict | None:
    """Current row plus version history, or None. Shape is storage-level."""
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "select name, version, spec, source, created_at, updated_at "
            "from app_contracts where name = %s",
            (name,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        cur = await conn.execute(
            "select version, spec, source, created_at from app_contract_versions "
            "where name = %s order by created_at desc, version",
            (name,),
        )
        versions = list(await cur.fetchall())
    detail = dict(row)
    detail["versions"] = _version_rows(versions, row["version"])
    return detail


# ── Writes ──────────────────────────────────────────────────────────────────

async def register_contract(parsed: ParsedContract, name: str) -> dict:
    """Persist a validated spec under `name`; returns the stored detail row."""
    name = validate_name(name)
    if name in get_registry().names():
        raise ContractConflict(f"contract name already exists: {name}")
    if name in file_contract_names():
        raise ContractConflict(f"contract name already exists: {name}")

    async with get_pool().connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                "select 1 from app_contracts where name = %s", (name,)
            )
            if await cur.fetchone() is not None:
                raise ContractConflict(f"contract name already exists: {name}")
            try:
                await conn.execute(
                    "insert into app_contracts (name, spec, version, source) "
                    "values (%s, %s, %s, 'upload')",
                    (name, Json(parsed.spec), parsed.version),
                )
                await conn.execute(
                    "insert into app_contract_versions "
                    "(name, version, spec, source) values (%s, %s, %s, 'upload')",
                    (name, parsed.version, Json(parsed.spec)),
                )
            except psycopg.errors.UniqueViolation as exc:
                # Lost a race with another register for the same name.
                raise ContractConflict(f"contract name already exists: {name}") from exc

    await load_into_registry()
    stored = await get_contract(name)
    if stored is None:  # pragma: no cover - insert above guarantees the row
        raise ContractUploadError("contract registration did not persist")
    return stored


async def delete_contract(name: str) -> None:
    name = validate_name(name)
    if name in file_contract_names():
        raise ContractImmutable(f"file-based contracts are immutable: {name}")
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "delete from app_contracts where name = %s returning name", (name,)
        )
        row = await cur.fetchone()
    if row is None:
        raise ContractNotFound(f"unknown contract: {name}")
    await load_into_registry()


# ── Registry overlay ────────────────────────────────────────────────────────

async def load_into_registry() -> int:
    """Replace the process-local uploaded overlay with the database's rows.

    Every stored spec is re-validated through contract_from_snapshot(), pinned
    to its stored hash. A corrupt or tampered row fails loudly here instead of
    arming a broken Pandera schema for the next run.
    """
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "select name, spec, version from app_contracts order by name"
        )
        rows = list(await cur.fetchall())
    contracts: dict[str, Contract] = {}
    for row in rows:
        contracts[row["name"]] = contract_from_snapshot(
            row["spec"], row["name"], row["version"]
        )
    set_uploaded_contracts(contracts)
    return len(contracts)


__all__ = [
    "ContractConflict",
    "ContractImmutable",
    "ContractNotFound",
    "ContractTooLarge",
    "ContractUploadError",
    "MAX_CONTRACT_BYTES",
    "MAX_CONTRACT_COLUMNS",
    "ParsedContract",
    "column_preview",
    "delete_contract",
    "ensure_schema",
    "file_contract_names",
    "get_contract",
    "list_contracts",
    "load_into_registry",
    "name_available",
    "parse_contract_upload",
    "register_contract",
    "validate_name",
]
