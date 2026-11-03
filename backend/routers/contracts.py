"""Upload, validate, register and manage data contracts.

This is the app-facing surface around services/contract_store.py. It never
writes an uploaded document to disk and never changes config/contracts/: a
registered contract is a Postgres row, and the only way it reaches the
pipeline is the in-process overlay in pipeline.contract (loaded at startup and
after every registration/deletion).

Route order matters for readability only: `/preview` is declared before
`/{name}` so the literal path is obvious to a reader.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from auth import require_token
from config import get_settings
from pipeline.contract import Contract, contract_from_snapshot, get_registry
from services import contract_store

log = logging.getLogger(__name__)

_schema_ready = False
_schema_lock: asyncio.Lock | None = None


def _get_schema_lock() -> asyncio.Lock:
    global _schema_lock
    if _schema_lock is None:
        _schema_lock = asyncio.Lock()
    return _schema_lock


async def _ensure_ready() -> None:
    """Make the tables + overlay usable on the first request of a process.

    Startup in main.py should already have done this; repeating it lazily is
    what keeps a cold Neon compute (or a missed startup hook) from turning
    every contract request into a 500. In production, runtime credentials lack
    DDL permissions, so schema migrations must be applied ahead of time.
    """
    global _schema_ready
    if _schema_ready:
        return
    try:
        async with _get_schema_lock():
            if not _schema_ready:
                if get_settings().auth_mode != "production":
                    await contract_store.ensure_schema()
                await contract_store.load_into_registry()
                _schema_ready = True
    except Exception:
        log.exception("contracts: schema/overlay init failed")
        raise HTTPException(status_code=503, detail="contract store unavailable, try again")


router = APIRouter(
    prefix="/api/app/contracts",
    tags=["contracts"],
    dependencies=[Depends(require_token), Depends(_ensure_ready)],
)


def _http(exc: ValueError) -> HTTPException:
    if isinstance(exc, contract_store.ContractTooLarge):
        return HTTPException(status_code=413, detail=str(exc))
    if isinstance(exc, contract_store.ContractConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, contract_store.ContractImmutable):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, contract_store.ContractNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _detail(name: str, version: str, source: str, spec: dict, contract: Contract,
            created_at=None, updated_at=None, versions: list[dict] | None = None) -> dict:
    return {
        "name": name,
        "version": version,
        "source": source,
        "deletable": source == "upload",
        "row_count_min": contract.row_count_min,
        "columns": list(contract.expected_dtypes),
        "column_count": len(contract.expected_dtypes),
        "column_preview": contract_store.column_preview(spec),
        "spec": spec,
        "created_at": _iso(created_at),
        "updated_at": _iso(updated_at),
        "versions": versions or [],
    }


def _file_detail(contract: Contract) -> dict:
    return _detail(
        contract.name,
        contract.version,
        "file",
        contract.spec,
        contract,
        versions=[{
            "version": contract.version,
            "source": "file",
            "column_count": len(contract.expected_dtypes),
            "current": True,
            "created_at": None,
        }],
    )


def _uploaded_detail(row: dict) -> dict:
    contract = contract_from_snapshot(row["spec"], row["name"], row["version"])
    versions = [
        {**entry, "created_at": _iso(entry.get("created_at"))}
        for entry in row.get("versions", [])
    ]
    return _detail(
        row["name"],
        row["version"],
        row.get("source", "upload"),
        row["spec"],
        contract,
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        versions=versions,
    )


@router.get("")
async def list_contracts() -> dict:
    """File contracts and uploaded contracts in one list, sorted by name."""
    registry = get_registry()
    uploaded = {row["name"]: row for row in await contract_store.list_contracts()}
    contracts = []
    for name in registry.names():
        contract = registry.get(name)
        stored = uploaded.get(name)
        if stored is None:
            contracts.append({
                "name": name,
                "version": contract.version,
                "source": "file",
                "deletable": False,
                "column_count": len(contract.expected_dtypes),
                "columns": list(contract.expected_dtypes),
                "created_at": None,
            })
        else:
            contracts.append({
                "name": stored["name"],
                "version": stored["version"],
                "source": stored.get("source", "upload"),
                "deletable": True,
                "column_count": stored["column_count"],
                "columns": stored["columns"],
                "created_at": _iso(stored.get("created_at")),
            })
    return {"contracts": contracts}


@router.post("/preview", status_code=200)
async def preview_contract(
    file: UploadFile = File(...),
    name: str | None = Form(None),
) -> dict:
    """Validate an upload and return its column preview without persisting it."""
    payload = await file.read(contract_store.MAX_CONTRACT_BYTES + 1)
    try:
        parsed = contract_store.parse_contract_upload(payload, name)
        available = (
            contract_store.name_available(parsed.name) if parsed.name else None
        )
    except ValueError as exc:
        raise _http(exc) from exc
    return {
        "name": parsed.name,
        "name_available": available,
        "format": parsed.format,
        "version": parsed.version,
        "row_count_min": parsed.spec.get("row_count_min", 0),
        "columns": parsed.columns,
        "column_count": parsed.column_count,
        "column_preview": contract_store.column_preview(parsed.spec),
        "spec": parsed.spec,
    }


@router.post("", status_code=201)
async def register_contract(
    name: str = Form(...),
    file: UploadFile = File(...),
) -> dict:
    """Validate and persist an uploaded contract under `name`."""
    payload = await file.read(contract_store.MAX_CONTRACT_BYTES + 1)
    try:
        parsed = contract_store.parse_contract_upload(payload, name)
        stored = await contract_store.register_contract(parsed, name)
    except ValueError as exc:
        raise _http(exc) from exc
    return _uploaded_detail(stored)


@router.get("/{name}")
async def get_contract(name: str) -> dict:
    try:
        contract_store.validate_name(name)
    except ValueError as exc:
        raise _http(exc) from exc

    if name in contract_store.file_contract_names():
        return _file_detail(get_registry().get(name))

    row = await contract_store.get_contract(name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown contract: {name}")
    return _uploaded_detail(row)


@router.delete("/{name}")
async def delete_contract(name: str) -> dict:
    try:
        contract_store.validate_name(name)
        await contract_store.delete_contract(name)
    except ValueError as exc:
        raise _http(exc) from exc
    return {"deleted": True, "name": name}
