"""Named CSV and read-only Postgres table sources with persistent snapshots."""

import csv
import json
from dataclasses import dataclass
from io import BytesIO, StringIO
import hashlib
import os
from pathlib import Path

import pandas as pd
import psycopg
from psycopg import sql
import yaml

from services import store, upload_store

ROOT = Path(__file__).resolve().parents[2]
CSV_DIR = ROOT / "data" / "sources"
SNAPSHOT_DIR = ROOT / ".run_snapshots"
SOURCE_CONFIG = ROOT / "config" / "sources.yaml"
PROFILE_LIMIT = 1000
FETCH_ROWS = 64


@dataclass(frozen=True)
class MaterializedSource:
    source_id: str
    path: str
    family: str | None
    ref: dict
    sha256: str


def _family(source_id: str) -> str | None:
    if source_id.startswith("day9_"):
        return "orders_current"
    if source_id in {"day1_clean.csv", "day8_revenue_shift.csv",
                     "day8_normal_control.csv", "day8_low_sample.csv"}:
        return "orders"
    return None


def _database_specs() -> dict:
    raw = yaml.safe_load(SOURCE_CONFIG.read_text(encoding="utf-8")) or {}
    return raw.get("database_tables") or {}


# App-expansion uploads (services/app_datasets table) are addressed as
# source_id = "upload_<dataset_id>". The underscore-delimited prefix keeps the
# ID a single Path-safe token (no scheme/colon to confuse filename checks) and
# cannot collide with the YAML catalog, bundled CSV filenames, or database
# table names.
UPLOAD_PREFIX = "upload_"


def _parse_upload_id(source_id: str) -> str:
    dataset_id = source_id[len(UPLOAD_PREFIX):]
    try:
        upload_store.parse_dataset_id(dataset_id)
    except upload_store.IngestionError as exc:
        # Malformed remainder is never a filesystem lookup — fail closed.
        raise ValueError("invalid uploaded dataset ID") from exc
    return dataset_id


async def _upload_source(source_id: str, contract_name: str, run_id: str) -> MaterializedSource:
    """Materialize a user-uploaded dataset through the existing snapshot flow.

    Contract binding is deliberately minimal: the trigger endpoint
    (POST /api/runs) has already validated `contract_name` against the
    registry, and this records that exact choice on the run. Column-level
    strictness is NOT enforced here on purpose — missing columns and wrong
    types are precisely the drift classes the pipeline diagnoses and heals,
    and rejecting them at materialization would disable the feature. The
    bytes are re-digest-checked on load, so what enters ``save_source_snapshot``
    is exactly what the user uploaded, foreign to anything on disk.
    """
    dataset_id = _parse_upload_id(source_id)
    row = await upload_store.get_dataset(dataset_id)
    if row is None:
        raise ValueError("uploaded dataset not found")
    try:
        payload = await upload_store.get_bytes(dataset_id)  # digest re-verified against the row
    except upload_store.IngestionError as exc:
        # Deleted or corrupted since the upload; POST /api/runs maps ValueError -> 422.
        raise ValueError(f"uploaded dataset unavailable: {exc.detail}") from exc
    digest = hashlib.sha256(payload).hexdigest()
    if digest != row["sha256"]:
        raise ValueError("uploaded dataset digest mismatch")
    await store.save_source_snapshot(run_id, source_id, payload, digest,
                                     row["row_count"], min(row["row_count"], PROFILE_LIMIT))
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    path = SNAPSHOT_DIR / f"{run_id}.csv"
    path.write_bytes(payload)
    ref = {"kind": "upload", "source_id": source_id, "dataset_id": dataset_id,
           "filename": row["filename"], "sha256": digest,
           "row_count": row["row_count"], "profile_rows": min(row["row_count"], PROFILE_LIMIT)}
    # family=None is the existing precedent for sources without a curated
    # (contract, family) baseline — no distribution/temporal rule applies
    # beyond the contract's own checks.
    return MaterializedSource(source_id, str(path), None, ref, digest)


def _json_specs() -> dict:
    raw = yaml.safe_load(SOURCE_CONFIG.read_text(encoding="utf-8")) or {}
    return raw.get("local_json") or {}


def list_sources(contract_name: str | None = None) -> list[dict]:
    csv_sources = [{"source_id": path.name, "kind": "bundled_csv", "family": _family(path.name),
                    "contract_name": "compact" if path.name == "compact_clean.csv" else "default"}
                   for path in sorted(CSV_DIR.glob("*.csv"))]
    db_sources = [{"source_id": name, "kind": "database_table", "family": spec.get("family"),
                   "contract_name": spec["contract_name"], "available": bool(os.environ.get(spec["connection_env"]))}
                  for name, spec in sorted(_database_specs().items())]
    json_sources = [{"source_id": name, "kind": "local_json", "family": spec.get("family"),
                     "contract_name": spec["contract_name"]}
                    for name, spec in sorted(_json_specs().items())]
    return [s for s in csv_sources + db_sources + json_sources
            if contract_name is None or s["contract_name"] == contract_name]


def flatten_record(record: dict, max_depth: int, separator: str = "__") -> dict:
    """Flatten objects only; duplicate targets and arrays fail closed."""
    if not isinstance(record, dict) or not separator or max_depth < 1:
        raise ValueError("invalid JSON flatten policy")
    flat = {}
    def visit(obj: dict, prefix: str, depth: int) -> None:
        if depth > max_depth:
            raise ValueError("JSON nesting exceeds depth cap")
        for key, value in obj.items():
            if not isinstance(key, str) or not key or separator in key:
                raise ValueError("JSON key conflicts with flatten separator")
            name = f"{prefix}{separator}{key}" if prefix else key
            if name in flat:
                raise ValueError("JSON flatten collision")
            if isinstance(value, dict):
                visit(value, name, depth + 1)
            elif isinstance(value, list):
                raise ValueError("unexpected JSON array")
            elif value is None or isinstance(value, (str, int, float, bool)):
                flat[name] = value
            else:
                raise ValueError("unsupported JSON value")
    visit(record, "", 1)
    return flat


async def _json_source(source_id: str, spec: dict, contract_name: str, run_id: str) -> MaterializedSource:
    if spec["contract_name"] != contract_name:
        raise ValueError("source does not belong to selected contract")
    file_name = spec.get("file")
    if not isinstance(file_name, str) or Path(file_name).name != file_name or not file_name.endswith(".json"):
        raise ValueError("invalid JSON source config")
    path = (CSV_DIR / file_name).resolve()
    if path.parent != CSV_DIR.resolve() or not path.is_file():
        raise ValueError("JSON source unavailable")
    byte_cap, row_cap = int(spec["byte_cap"]), int(spec["row_cap"])
    if not 1 <= byte_cap <= 10000000 or not 1 <= row_cap <= 100000:
        raise ValueError("invalid JSON source cap")
    with path.open("rb") as handle:
        raw = handle.read(byte_cap + 1)
    if len(raw) > byte_cap:
        raise ValueError("JSON source exceeds byte cap")
    try:
        document = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSON source") from exc
    records = document
    for segment in spec["records_path"].split("."):
        if not isinstance(records, dict) or segment not in records:
            raise ValueError("JSON records path missing")
        records = records[segment]
    if not isinstance(records, list) or len(records) > row_cap:
        raise ValueError("JSON records must be an array within row cap")
    max_depth = int(spec["max_depth"])
    separator = spec.get("separator", "__")
    flattened = [flatten_record(record, max_depth, separator) for record in records]
    columns = sorted({key for record in flattened for key in record})
    if not columns:
        raise ValueError("JSON source has no columns")
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for record in flattened:
        writer.writerow(record)
        if output.tell() > byte_cap:
            raise ValueError("normalized JSON exceeds byte cap")
    payload = output.getvalue().encode("utf-8")
    if len(payload) > byte_cap:
        raise ValueError("normalized JSON exceeds byte cap")
    digest = hashlib.sha256(payload).hexdigest()
    await store.save_source_snapshot(run_id, source_id, payload, digest, len(records), min(len(records), PROFILE_LIMIT))
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"{run_id}.csv"
    snapshot_path.write_bytes(payload)
    ref = {"kind": "local_json", "source_id": source_id, "sha256": digest,
           "row_count": len(records), "profile_rows": min(len(records), PROFILE_LIMIT)}
    return MaterializedSource(source_id, str(snapshot_path), spec.get("family"), ref, digest)


def _csv_source(source_id: str, contract_name: str) -> MaterializedSource:
    if Path(source_id).name != source_id or not source_id.lower().endswith(".csv"):
        raise ValueError("unknown source ID")
    path = (CSV_DIR / source_id).resolve()
    if path.parent != CSV_DIR.resolve() or not path.is_file():
        raise ValueError("unknown source ID")
    expected = "compact" if source_id == "compact_clean.csv" else "default"
    if expected != contract_name:
        raise ValueError("source does not belong to selected contract")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return MaterializedSource(source_id, str(path), _family(source_id),
                              {"kind": "bundled_csv", "source_id": source_id}, digest)


async def materialize_source(source_id: str, contract_name: str, run_id: str) -> MaterializedSource:
    if not isinstance(source_id, str) or not source_id or Path(source_id).name != source_id or any(c in source_id for c in ("/", "\\", "\0")):
        raise ValueError("unknown source ID")
    if source_id.startswith(UPLOAD_PREFIX):
        return await _upload_source(source_id, contract_name, run_id)
    if source_id in _json_specs():
        return await _json_source(source_id, _json_specs()[source_id], contract_name, run_id)
    if source_id not in _database_specs():
        return _csv_source(source_id, contract_name)
    spec = _database_specs()[source_id]
    if spec["contract_name"] != contract_name:
        raise ValueError("source does not belong to selected contract")
    connection_url = os.environ.get(spec["connection_env"])
    if not connection_url:
        raise ValueError("database source connection is not configured")
    cap = int(spec["row_cap"])
    if not 1 <= cap <= 100000:
        raise ValueError("invalid database source row cap")
    byte_cap = int(spec["byte_cap"])
    if not 1 <= byte_cap <= 100000000:
        raise ValueError("invalid database source byte cap")
    relation = sql.Identifier(spec["schema"], spec["table"])
    key = sql.Identifier(spec["order_key"])
    query = sql.SQL("select * from {} order by {} limit %s").format(relation, key)
    output = BytesIO()
    row_count = 0
    seen_keys = set()
    columns = None
    try:
        async with await psycopg.AsyncConnection.connect(connection_url, connect_timeout=10) as conn:
            async with conn.transaction():
                await conn.execute("set transaction isolation level repeatable read, read only")
                await conn.execute("select set_config('statement_timeout', %s, true)", (str(int(spec["statement_timeout_ms"])),))
                async with conn.cursor(name="p2_snapshot") as cursor:
                    await cursor.execute(query, (cap + 1,))
                    columns = [c.name for c in cursor.description]
                    if spec["order_key"] not in columns:
                        raise ValueError("database order key is missing")
                    key_index = columns.index(spec["order_key"])
                    header = StringIO(newline="")
                    csv.writer(header).writerow(columns)
                    output.write(header.getvalue().encode("utf-8"))
                    while True:
                        batch = await cursor.fetchmany(FETCH_ROWS)
                        if not batch:
                            break
                        for row in batch:
                            row_count += 1
                            if row_count > cap:
                                raise ValueError(f"database source exceeds row cap {cap}")
                            key_value = row[key_index]
                            if key_value is None or key_value in seen_keys:
                                raise ValueError("database order key must be unique and non-null")
                            seen_keys.add(key_value)
                            encoded = StringIO(newline="")
                            csv.writer(encoded).writerow(row)
                            row_bytes = encoded.getvalue().encode("utf-8")
                            if output.tell() + len(row_bytes) > byte_cap:
                                raise ValueError(f"database source exceeds byte cap {byte_cap}")
                            output.write(row_bytes)
    except (psycopg.Error, OSError, TimeoutError) as exc:
        raise SourceUnavailable(f"database source read failed: {type(exc).__name__}") from exc
    payload = output.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    await store.save_source_snapshot(run_id, source_id, payload, digest, row_count, min(row_count, PROFILE_LIMIT))
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    path = SNAPSHOT_DIR / f"{run_id}.csv"
    path.write_bytes(payload)
    ref = {"kind": "database_table", "source_id": source_id, "sha256": digest,
           "row_count": row_count, "profile_rows": min(row_count, PROFILE_LIMIT)}
    return MaterializedSource(source_id, str(path), spec.get("family"), ref, digest)


class SourceUnavailable(RuntimeError):
    pass


async def ensure_source_path(state: dict) -> str:
    """Ensure the source snapshot file exists on disk and its SHA-256 matches."""
    ref = state.get("source_ref") or {}
    path = Path(state["source_path"])
    if ref.get("kind") not in {"database_table", "local_json", "external_batch", "upload"}:
        return str(path)
    expected = ref["sha256"]
    if not path.is_file():
        payload = await store.load_source_snapshot(state["run_id"], expected)
        SNAPSHOT_DIR.mkdir(exist_ok=True)
        path.write_bytes(payload)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("persisted source snapshot digest mismatch")
    return str(path)


def profile_frame(path: str) -> pd.DataFrame:
    """Deterministic first 1000 rows of the already ordered complete snapshot."""
    return pd.read_csv(path, nrows=PROFILE_LIMIT)
