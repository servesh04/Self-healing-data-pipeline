"""Upload, list, preview and delete bounded CSV datasets — the HTTP surface
behind the app expansion's ingestion page (docs/app-proposals/ingestion.md).

Design points that are easy to get wrong, deliberately encoded here:

* The client-side file field is the only input. The client-supplied filename
  is metadata only — ``upload_store.sanitize_filename`` reduces it to a
  basename and no code path ever opens a path supplied by a request.
* Caps are enforced server-side (5 MiB bytes / 5,000 data rows); the browser
  pre-check is only fast feedback.
* Pipeline triggering is NOT re-implemented: this router only stores
  datasets. ``POST /api/runs`` with ``source_id = "upload_<dataset_id>"`
  resolves through ``pipeline.sources`` exactly like every other source kind,
  and the page navigates to the existing ``/runs/<run_id>`` view.
* Schema creation is lazy-but-once (services/upload_store.ensure_schema),
  the same guarded pattern routers/runs.py applies to the checkpointer, so
  the feature needs no backend/main.py change to work.
"""

import logging

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from auth import require_token
from services import upload_store
from services.upload_store import IngestionError, MAX_BYTES

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/app/datasets",
    tags=["datasets"],
    dependencies=[Depends(require_token)],
)


def _map_ingestion_error(exc: IngestionError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _map_store_failure(exc: Exception) -> HTTPException:
    log.exception("datasets: store unavailable")
    return HTTPException(status_code=503, detail="dataset store unavailable")


async def ingest_upload(filename: str | None, payload: bytes) -> dict:
    """Shared upload path (endpoint body minus the multipart plumbing).

    Kept standalone so the offline test suite can drive it directly, and so
    the HTTP layer stays a thin error-mapping shell.
    """
    try:
        record, reused = await upload_store.create_dataset(filename, payload)
    except IngestionError as exc:
        raise _map_ingestion_error(exc) from exc
    except psycopg.Error as exc:
        raise _map_store_failure(exc) from exc
    status = 200 if reused else 201
    if reused:
        log.info("datasets: identical retry returned existing dataset %s", record["dataset_id"])
    return JSONResponse(status_code=status, content=record)


@router.post("")
async def upload_dataset(file: UploadFile) -> JSONResponse:
    """Upload one bounded CSV file (multipart/form-data, field name ``file``).

    200 with ``reused: true`` for an identical retry (same bytes + filename);
    201 for a fresh dataset; 413 over either cap; 422 for anything that is
    not acceptable CSV content.
    """
    if file is None:
        raise HTTPException(status_code=422, detail="a CSV file part named 'file' is required")
    # Read one byte past the cap so a dishonest huge upload is rejected by
    # content length in analyze_csv, not by a spool it cannot bound.
    payload = await file.read(MAX_BYTES + 1)
    try:
        await file.close()
    except Exception:  # noqa: BLE001 — closing the spool must never mask validation
        log.exception("datasets: closing upload spool failed")
    return await ingest_upload(file.filename, payload)


@router.get("")
async def list_recent_datasets(limit: int = Query(20, ge=1, le=upload_store.PREVIEW_MAX_LIMIT)) -> dict:
    try:
        return {"datasets": await upload_store.list_datasets(limit)}
    except psycopg.Error as exc:
        raise _map_store_failure(exc) from exc


@router.get("/{dataset_id}")
async def get_dataset(dataset_id: str) -> dict:
    try:
        record = await upload_store.get_dataset(dataset_id)
    except IngestionError as exc:
        raise _map_ingestion_error(exc) from exc
    except psycopg.Error as exc:
        raise _map_store_failure(exc) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="uploaded dataset not found")
    return record


@router.get("/{dataset_id}/preview")
async def preview_dataset(
    dataset_id: str,
    limit: int = Query(20, ge=1, le=upload_store.PREVIEW_MAX_LIMIT),
) -> dict:
    if limit < 1 or limit > upload_store.PREVIEW_MAX_LIMIT:
        raise ValueError(f"preview limit must be between 1 and {upload_store.PREVIEW_MAX_LIMIT}")
    try:
        record, rows, row_count = await upload_store.read_rows(dataset_id, limit)
    except IngestionError as exc:
        raise _map_ingestion_error(exc) from exc
    except psycopg.Error as exc:
        raise _map_store_failure(exc) from exc
    return {
        "dataset_id": record["dataset_id"],
        "filename": record["filename"],
        "sha256": record["sha256"],
        "byte_size": record["byte_size"],
        "row_count": row_count,
        "rows": rows,
        "rows_returned": len(rows),
        "columns": record["columns"],
        "truncated": row_count > len(rows),
    }


@router.delete("/{dataset_id}")
async def delete_dataset(dataset_id: str) -> dict:
    """Delete the dataset record. Resumable runs are unaffected: triggering a
    run copies the bytes into the run-scoped immutable ``source_snapshots``
    table synchronously, so only *new* triggers on this id would fail.
    """
    try:
        deleted = await upload_store.delete_dataset(dataset_id)
    except IngestionError as exc:
        raise _map_ingestion_error(exc) from exc
    except psycopg.Error as exc:
        raise _map_store_failure(exc) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="uploaded dataset not found")
    return {"deleted": True, "dataset_id": dataset_id}
