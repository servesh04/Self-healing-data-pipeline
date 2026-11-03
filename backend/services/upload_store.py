"""Bounded CSV uploads as Postgres-backed immutable dataset snapshots.

The app expansion's ingestion feature: a user uploads a small CSV from the
browser, previews it, selects a contract, and triggers the existing
LangGraph pipeline with ``source_id = "upload_<dataset_id>"``. Three rules
drive every decision here:

* **Bytes live in Postgres, nowhere else.** Render's free tier has no
  persistent filesystem, and the pipeline's snapshot mechanism
  (``store.save_source_snapshot`` / ``source_snapshots``) already expects
  ``bytea`` payloads. ``app_datasets`` is the dataset-level index for the
  same bytes, so the upload page can list, preview and re-run a dataset
  across many runs.
* **Every upload is bounded and self-verifying.** Caps are enforced
  server-side (413/422), the original bytes are preserved verbatim, and a
  SHA-256 digest is recomputed on every read. A stale/corrupted row fails
  closed instead of feeding the pipeline silently different data.
* **A retry cannot create duplicates.** ``unique (sha256, filename)`` plus
  ``insert ... on conflict do nothing`` means re-uploading the identical
  file returns the *existing* record (``reused: true``) instead of
  inserting a twin row. Different bytes under the same filename are a
  deliberately new dataset (a different digest is different content).

No endpoint ever accepts a filesystem path: the client sends a filename
that is treated as pure metadata (basename, never opened on disk).
"""

import asyncio
from pathlib import PurePosixPath, PureWindowsPath
import csv
import hashlib
import io
import re
import uuid

import pandas as pd
from psycopg.types.json import Json

from config import get_settings
from db import get_pool

# Hard caps (server-side; the browser adds fast client-side feedback but the
# server is the only enforcement). Same spirit — and row limit — as the P6
# staged-batch caps in services/external_store.py.
MAX_BYTES = 5 * 1024 * 1024          # 5 MiB of raw bytes
MAX_ROWS = 5_000                     # data rows, excluding the header
MAX_COLUMNS = 256
PREVIEW_MAX_LIMIT = 50               # preview/sample limit clamp

_CSV_SUFFIX = ".csv"
_DATASET_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_TYPE_NAMES = {
    "integer": "integer",
    "float": "float",
    "boolean": "boolean",
    "datetime": "datetime",
}


class IngestionError(Exception):
    """Validation failure with a ready-made HTTP mapping.

    ``status_code``: 413 for cap breaches, 422 for anything that is
    fundamentally not an acceptable CSV. Inside the pipeline
    (``pipeline/sources.py``) upload problems are re-raised as bare
    ``ValueError`` so ``POST /api/runs`` keeps mapping them to 422 with no
    change to that shared router.
    """

    def __init__(self, detail: str, status_code: int = 422):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def _friendly_type(dtype: object) -> str:
    kind = str(dtype).split("[")[0].lower()
    if kind.startswith("int") or kind == "integer":
        return "integer"
    if kind.startswith("float"):
        return "float"
    if kind.startswith("bool"):
        return "boolean"
    if "date" in kind or "time" in kind:
        return "datetime"
    return "string"


def _infer_columns(names: list[str], sample_rows: list[list[str]]) -> list[dict]:
    """Infer a friendly type per column from the first rows only.

    Inference is deliberately best-effort and preview-scoped: pandas is run
    over the re-serialized sample (values are quoted back exactly as
    ``csv`` would), and any parsing quirk (ragged rows, restart sequences)
    degrades to ``string`` rather than rejecting the upload — rejecting a
    type the pipeline can heal is this app's core feature.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(names)
    for row in sample_rows:
        writer.writerow(row)
    dtypes = {}
    try:
        frame = pd.read_csv(io.StringIO(buffer.getvalue()))
        dtypes = {name: frame.dtypes.get(name) for name in names if name in frame.dtypes}
    except Exception:  # noqa: BLE001 — pandas parse errors are a degraded preview, not a rejection
        return [{"name": name, "type": "string"} for name in names]
    return [{"name": name, "type": _friendly_type(dtype)} for name, dtype in dtypes.items()]


def analyze_csv(payload: bytes) -> dict:
    """Pure validation + analysis of uploaded bytes. No I/O, no DB.

    Reads the file exactly once streaming through ``csv`` in strict mode,
    counts data rows against the cap, samples the first rows for type
    inference, and raises :class:`IngestionError` with a 413/422 status for
    everything that cannot be handed to the pipeline honestly.
    """
    if not payload:
        raise IngestionError("uploaded file is empty (zero bytes)")
    if len(payload) > MAX_BYTES:
        raise IngestionError(f"file exceeds the {MAX_BYTES // (1024 * 1024)} MiB byte cap", 413)
    try:
        text = payload.decode("utf-8-sig")  # BOM-tolerant UTF-8, same as the P6 staged batches
    except UnicodeError as exc:
        raise IngestionError("file is not valid UTF-8 CSV") from exc
    if "\x00" in text:
        raise IngestionError("file contains NUL bytes (malformed CSV)")

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise IngestionError("CSV header is missing") from exc
    except csv.Error as exc:
        raise IngestionError("malformed CSV") from exc
    if not header or all(not name.strip() for name in header):
        raise IngestionError("CSV header is missing")
    if len(header) != len(set(header)):
        raise IngestionError("CSV header has duplicate column names")
    if len(header) > MAX_COLUMNS:
        raise IngestionError(f"CSV has more than {MAX_COLUMNS} columns", 413)

    row_count = 0
    sample_rows: list[list[str]] = []
    try:
        for row in reader:
            row_count += 1
            if row_count > MAX_ROWS:
                raise IngestionError(f"file exceeds the {MAX_ROWS:,}-row cap", 413)
            if len(sample_rows) < PREVIEW_MAX_LIMIT:
                sample_rows.append(row)
    except csv.Error as exc:
        raise IngestionError("malformed CSV") from exc
    if row_count == 0:
        raise IngestionError("empty CSV: header only, no data rows")

    return {"columns": _infer_columns(header, sample_rows), "row_count": row_count}


def parse_dataset_id(dataset_id: str) -> str:
    """Fail closed on anything that is not the exact id a dataset row owns."""
    if not isinstance(dataset_id, str) or not _DATASET_ID.fullmatch(dataset_id):
        raise IngestionError("invalid dataset ID")
    return dataset_id


def sanitize_filename(filename: str | None) -> str:
    """Normalize the client-supplied name to a display-only basename.

    The name is never a filesystem path: both POSIX and Windows separator
    forms are reduced to their last component and the result must be a
    non-empty ``.csv`` name.
    """
    name = (filename or "").strip()
    if not name:
        raise IngestionError("upload is missing a filename")
    name = PureWindowsPath(PurePosixPath(name).name).name.split("\\")[-1].strip()
    if not name:
        raise IngestionError("upload is missing a filename")
    if not name.lower().endswith(_CSV_SUFFIX):
        raise IngestionError("only .csv files are accepted")
    return name


# ── Idempotent schema lazy-init ─────────────────────────────────────────────
# runs.py applies the same pattern for the checkpointer: the router (and
# POST /api/runs after a fresh deploy) can be hit before any lifespan hook
# ran, so schema creation is a cheap guarded one-shot per process.
_schema_ready = False
_schema_lock = asyncio.Lock()


async def ensure_schema() -> None:
    """Create ``app_datasets`` if needed. In production, tables are created ahead of time."""
    global _schema_ready
    if _schema_ready:
        return
    try:
        if get_settings().auth_mode == "production":
            _schema_ready = True
            return
    except Exception:
        pass
    async with get_pool().connection() as conn:
        await conn.execute("""
            create table if not exists app_datasets (
                id          text primary key,
                filename    text not null,
                sha256      text not null,
                payload     bytea not null,
                byte_size   integer not null,
                row_count   integer not null,
                columns     jsonb not null default '[]'::jsonb,
                created_at  timestamptz not null default now()
            )
        """)
        await conn.execute(
            "create unique index if not exists app_datasets_content_identity "
            "on app_datasets (sha256, filename)"
        )
    _schema_ready = True


_RECORD_COLUMNS = "id, filename, sha256, byte_size, row_count, columns, created_at"


def record_shape(row: dict, reused: bool | None = None) -> dict:
    """API projection of a dataset row — never the payload bytes."""
    record = {
        "dataset_id": row["id"],
        "filename": row["filename"],
        "sha256": row["sha256"],
        "byte_size": row["byte_size"],
        "row_count": row["row_count"],
        "columns": row["columns"],
        "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else row["created_at"],
    }
    if reused is not None:
        record["reused"] = reused
    return record


async def create_dataset(filename: str | None, payload: bytes) -> tuple[dict, bool]:
    """Insert an upload, or return the existing row on an identical retry.

    Identity is ``(sha256, filename)``: the digest pins the exact bytes, the
    name keeps two different files with coincidentally equal bytes apart.
    Returns ``(record, reused)`` where ``reused`` tells the client the
    upload was deduplicated rather than persisted fresh.
    """
    name = sanitize_filename(filename)
    analysis = analyze_csv(payload)          # byte/row caps + content validation
    digest = hashlib.sha256(payload).hexdigest()
    dataset_id = str(uuid.uuid4())
    await ensure_schema()

    async with get_pool().connection() as conn:
        cur = await conn.execute(
            """insert into app_datasets (id, filename, sha256, payload, byte_size, row_count, columns)
               values (%s, %s, %s, %s, %s, %s, %s)
               on conflict (sha256, filename) do nothing""",
            (dataset_id, name, digest, payload, len(payload),
             analysis["row_count"], Json(analysis["columns"])),
        )
        # rowcount 0 ⇒ an identical (sha256, filename) upload already exists:
        # return that row rather than persisting a twin record.
        if cur.rowcount == 1:
            cur = await conn.execute(f"select {_RECORD_COLUMNS} from app_datasets where id = %s",
                                     (dataset_id,))
            reused = False
        else:
            cur = await conn.execute(
                f"select {_RECORD_COLUMNS} from app_datasets where sha256 = %s and filename = %s",
                (digest, name),
            )
            reused = True
        row = await cur.fetchone()
    if row is None:
        raise IngestionError("dataset creation conflict", 409)
    return record_shape(row, reused=reused), reused


async def get_dataset(dataset_id: str) -> dict | None:
    """Metadata-only lookup, or ``None`` when unknown/deleted."""
    parse_dataset_id(dataset_id)
    await ensure_schema()
    async with get_pool().connection() as conn:
        cur = await conn.execute(f"select {_RECORD_COLUMNS} from app_datasets where id = %s", (dataset_id,))
        row = await cur.fetchone()
    return record_shape(row) if row else None


async def get_bytes(dataset_id: str) -> bytes:
    """The preserved original bytes, digest-verified on every read."""
    parse_dataset_id(dataset_id)
    await ensure_schema()
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "select payload, sha256, row_count from app_datasets where id = %s", (dataset_id,))
        row = await cur.fetchone()
    if row is None:
        raise IngestionError("uploaded dataset not found", 404)
    payload = bytes(row["payload"])
    if hashlib.sha256(payload).hexdigest() != row["sha256"]:
        raise IngestionError("stored dataset fails its SHA-256 digest (digest mismatch)", 409)
    return payload


async def read_rows(dataset_id: str, limit: int) -> tuple[dict, list[dict], int]:
    """Metadata + first ``limit`` data rows from the stored bytes, one call.

    The full payload is re-counted against the stored ``row_count`` (cheap at
    the 5,000-row cap) so a corrupted row fails the preview instead of
    showing the pipeline data that disagrees with the dataset metadata.
    """
    if limit < 1 or limit > PREVIEW_MAX_LIMIT:
        raise IngestionError(f"preview limit must be between 1 and {PREVIEW_MAX_LIMIT}", 422)
    row = await get_dataset(dataset_id)
    if row is None:
        raise IngestionError("uploaded dataset not found", 404)
    payload = await get_bytes(dataset_id)
    parsed, _header = extract_rows(payload)
    if len(parsed) != row["row_count"]:
        raise IngestionError("stored dataset row count does not match its payload", 409)
    return row, parsed[:limit], row["row_count"]


def extract_rows(payload: bytes) -> tuple[list[dict], list[str]]:
    """Parse all data rows (bounded by the upload-time 5,000-row cap).

    Values are the preserved original strings (never re-typed), so the
    preview shows exactly the text the pipeline will receive.
    """
    try:
        text = payload.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(reader)
        rows = [
            {name: value for name, value in zip(header, row) if name}
            for row in reader
        ]
    except (UnicodeError, csv.Error, StopIteration) as exc:
        raise IngestionError("stored dataset is no longer parseable") from exc
    return rows, header


async def delete_dataset(dataset_id: str) -> bool:
    """Remove one dataset row; runs are unaffected (per-run snapshots are copied)."""
    parse_dataset_id(dataset_id)
    await ensure_schema()
    async with get_pool().connection() as conn:
        cur = await conn.execute("delete from app_datasets where id = %s", (dataset_id,))
    return cur.rowcount == 1


async def list_datasets(limit: int = 20) -> list[dict]:
    """Newest-first metadata projection for the page's recent-datasets panel."""
    await ensure_schema()
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            f"select {_RECORD_COLUMNS} from app_datasets order by created_at desc limit %s",
            (limit,),
        )
        rows = await cur.fetchall()
    return [record_shape(row) for row in rows]
