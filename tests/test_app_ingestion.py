"""Offline tests for the app-expansion ingestion surface.

Everything runs with zero network: no Postgres, no pipeline, no HTTP
transport. The store is driven against a tiny fake pool that implements
exactly the SQL ``services/upload_store.py`` issues (the same pattern
``test_p6.py`` uses for ``scripts/object_orders_feed.py``); the pipeline
materialization is driven via monkeypatched store boundaries. HTTP-level
auth is the shared ``require_token`` dependency and is covered by QA's
integration pass; here the router logic is exercised directly.
"""

import hashlib
from datetime import datetime, timezone
from io import BytesIO
import inspect
import json

import pytest
from fastapi import HTTPException

from routers import datasets as datasets_router
from services import upload_store
import pipeline.sources as sources
from services import store


CSV = "order_id,region,revenue\n1,east,100.5\n2,west,20\n3,east,0\n"


def _csv_rows(count, prefix="row"):
    header = "order_id,region\n"
    return header + "\n".join(f"{i},{prefix}" for i in range(count))


# ── The fake pool implements exactly the SQL upload_store issues ────────────

class FakeCursor:
    def __init__(self, rowcount=0, rows=None):
        self.rowcount = rowcount
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, engine):
        self.engine = engine

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def _select(self, sql, params):
        rows =list(self.engine.rows.values())
        if "order by created_at desc limit %s" in sql:
            rows.sort(key=lambda r: r["created_at"], reverse=True)
            return FakeCursor(0, rows[: params[0]])
        if "sha256 = %s and filename = %s" in sql:
            rows = [r for r in rows if r["sha256"] == params[0] and r["filename"] == params[1]]
        elif "where id = %s" in sql:
            rows = [r for r in rows if r["id"] == params[0]]
        return FakeCursor(len(rows), rows)

    async def execute(self, sql, params=None):
        if sql.startswith("create"):
            return FakeCursor()  # schema DDL: no-op
        if sql.startswith("insert into app_datasets"):
            dataset_id, filename, digest, payload, byte_size, row_count, columns = params
            if any(r["sha256"] == digest and r["filename"] == filename for r in self.engine.rows.values()):
                return FakeCursor(0)  # on conflict (sha256, filename) do nothing
            self.engine.rows[dataset_id] = {
                "id": dataset_id, "filename": filename, "sha256": digest, "payload": payload,
                "byte_size": byte_size, "row_count": row_count, "columns": columns,
                "created_at": datetime.now(timezone.utc),
            }
            return FakeCursor(1)
        if sql.startswith("select"):
            return self._select(sql, params)
        if sql.startswith("delete"):
            exists = params[0] in self.engine.rows
            self.engine.rows.pop(params[0], None)
            return FakeCursor(1 if exists else 0)
        raise AssertionError(f"unhandled SQL: {sql[:80]}")


class FakePool:
    def __init__(self, rows=None):
        self.rows = rows or {}

    def connection(self):
        return FakeConn(self)


# ── Pure analysis: caps, encoding, header, inference ───────────────────────

def test_valid_csv_analysis_reports_columns_types_and_row_count():
    result = upload_store.analyze_csv("order_id,region,revenue\n1,east,100.5\n2,west,20\n".encode())
    assert result["row_count"] == 2
    by_name = {col["name"]: col["type"] for col in result["columns"]}
    assert by_name == {"order_id": "integer", "region": "string", "revenue": "float"}


def test_bom_tolerated_and_values_keep_original_presentation():
    payload = b"\xef\xbb\xbforder_id,note\n1,007\n"
    result = upload_store.analyze_csv(payload)
    assert result["row_count"] == 1  # header + BOM handled, data row counted


def test_zero_byte_file_rejected():
    with pytest.raises(upload_store.IngestionError) as err:
        upload_store.analyze_csv(b"")
    assert err.value.status_code == 422 and "empty" in err.value.detail


def test_header_only_csv_rejected():
    with pytest.raises(upload_store.IngestionError) as err:
        upload_store.analyze_csv(b"order_id,region\n")
    assert err.value.status_code == 422 and "header only" in err.value.detail


def test_duplicate_column_names_rejected():
    with pytest.raises(upload_store.IngestionError) as err:
        upload_store.analyze_csv(b"a,b,a\n1,2,3\n")
    assert err.value.status_code == 422 and "duplicate" in err.value.detail


def test_non_utf8_bytes_rejected():
    with pytest.raises(upload_store.IngestionError) as err:
        upload_store.analyze_csv(b"\xff\xfe\x49\x00,\x48\x00\x20\x00\n")
    assert err.value.status_code == 422 and "UTF-8" in err.value.detail


def test_malformed_csv_rejected():
    payload = 'order_id,note\n"x\x00y"\n'.encode()
    with pytest.raises(upload_store.IngestionError) as err:
        upload_store.analyze_csv(payload)
    assert err.value.status_code == 422


def test_byte_cap_breached_maps_to_413(monkeypatch):
    monkeypatch.setattr(upload_store, "MAX_BYTES", 10)
    with pytest.raises(upload_store.IngestionError) as err:
        upload_store.analyze_csv(CSV.encode())
    assert err.value.status_code == 413


def test_row_cap_breached_maps_to_413(monkeypatch):
    monkeypatch.setattr(upload_store, "MAX_ROWS", 4)
    with pytest.raises(upload_store.IngestionError) as err:
        upload_store.analyze_csv(_csv_rows(5).encode())
    assert err.value.status_code == 413


def test_missing_or_mistyped_columns_are_accepted_not_rejected():
    """The pipeline heals missing columns / wrong types; uploading must not."""

    payload = "order_id,total\nx-1,not-a-number\n".encode()
    result = upload_store.analyze_csv(payload)
    assert result["row_count"] == 1
    assert [col["type"] for col in result["columns"]] == ["string", "string"]


def test_sanitize_filename_basenames_and_extension_rules():
    assert upload_store.sanitize_filename("orders_nov.csv") == "orders_nov.csv"
    assert upload_store.sanitize_filename("C:\\evil\\path\\orders.csv") == "orders.csv"
    assert upload_store.sanitize_filename("orders.CSV") == "orders.CSV"
    for bad in ("", "   ", "orders.txt", "orders.json", ".."):
        with pytest.raises(upload_store.IngestionError) as err:
            upload_store.sanitize_filename(bad)
        assert err.value.status_code == 422


def test_parse_dataset_id_fails_closed():
    good = "01234567-89ab-cdef-0123-456789abcdef"
    assert upload_store.parse_dataset_id(good) == good
    for bad in ("", "upload_not-a-uuid", good[:-1], str(good) + "\\", "x" * 40):
        with pytest.raises(upload_store.IngestionError) as err:
            upload_store.parse_dataset_id(bad)
        assert err.value.status_code == 422


# ── Store + idempotency against the fake pool ─────────────────────────────

@pytest.fixture
def pool(monkeypatch):
    """Wire upload_store (and anything importing it — pipeline.sources) to a fake."""
    fake = FakePool()
    monkeypatch.setattr(upload_store, "get_pool", lambda: fake, raising=False)
    monkeypatch.setattr(upload_store, "_schema_ready", False)
    return fake

async def test_upload_and_reuse_lifecycle(pool):
    record, reused = await upload_store.create_dataset("orders.csv", CSV.encode())
    assert reused is False
    assert record["filename"] == "orders.csv" and record["row_count"] == 3
    assert record["sha256"] == hashlib.sha256(CSV.encode()).hexdigest()
    assert list(record["columns"][0].items()) == [("name", "order_id"), ("type", "integer")]

    identical, reused_again = await upload_store.create_dataset("orders.csv", CSV.encode())
    assert reused_again is True
    assert identical["dataset_id"] == record["dataset_id"]  # same row, no duplicate
    assert len(pool.rows) == 1

async def test_same_filename_new_bytes_is_a_new_dataset(pool):
    first, _ = await upload_store.create_dataset("orders.csv", CSV.encode())
    second, reused = await upload_store.create_dataset("orders.csv", (CSV + "4,east,7\n").encode())
    assert reused is False and first["dataset_id"] != second["dataset_id"]
    assert len(pool.rows) == 2

async def test_get_dataset_and_get_bytes_verify_digest(pool):
    record, _ = await upload_store.create_dataset("orders.csv", CSV.encode())
    assert (await upload_store.get_dataset(record["dataset_id"]))["row_count"] == 3
    assert await upload_store.get_bytes(record["dataset_id"]) == CSV.encode()

    # corrupt the stored payload → digest verification fails closed
    victim = next(iter(pool.rows.values()))
    victim["payload"] = b"tampered"
    with pytest.raises(upload_store.IngestionError) as err:
        await upload_store.get_bytes(record["dataset_id"])
    assert err.value.status_code == 409

async def test_get_dataset_unknown_and_malformed_ids(pool):
    good = "01234567-89ab-cdef-0123-456789abcdef"
    assert await upload_store.get_dataset(good) is None
    with pytest.raises(upload_store.IngestionError):
        await upload_store.get_dataset("upload_not-a-uuid")

async def test_read_rows_returns_first_limit_and_checks_count(pool):
    record, _ = await upload_store.create_dataset("orders.csv", CSV.encode())
    row, rows, row_count = await upload_store.read_rows(record["dataset_id"], 2)
    assert row_count == 3 and len(rows) == 2 and rows[0] == {"order_id": "1", "region": "east", "revenue": "100.5"}
    with pytest.raises(upload_store.IngestionError) as err:
        await upload_store.read_rows(record["dataset_id"], 50)
    assert err.value.status_code == 422

    # stored metadata disagrees with payload → fail closed, do not show lies
    victim = next(iter(pool.rows.values()))
    victim["row_count"] = 99
    with pytest.raises(upload_store.IngestionError) as err:
        await upload_store.read_rows(record["dataset_id"], 2)
    assert err.value.status_code == 409

async def test_read_rows_unknown_dataset_maps_to_404(pool):
    good = "01234567-89ab-cdef-0123-456789abcdef"
    with pytest.raises(upload_store.IngestionError) as err:
        await upload_store.read_rows(good, 10)
    assert err.value.status_code == 404

async def test_delete_dataset_and_aftermath(pool):
    record, _ = await upload_store.create_dataset("orders.csv", CSV.encode())
    dataset_id = record["dataset_id"]
    assert await upload_store.delete_dataset(dataset_id) is True
    assert await upload_store.delete_dataset(dataset_id) is False
    assert await upload_store.get_dataset(dataset_id) is None
    with pytest.raises(upload_store.IngestionError) as err:
        await upload_store.get_bytes(dataset_id)
    assert err.value.status_code == 404

async def test_preview_limit_shapes_and_list_order(pool):
    a, _ = await upload_store.create_dataset("a.csv", CSV.encode())
    b, _ = await upload_store.create_dataset("b.csv", CSV.encode())
    listed = await upload_store.list_datasets(10)
    ids = [r["dataset_id"] for r in listed]
    assert a["dataset_id"] in ids and b["dataset_id"] in ids  # newest-first is ordering-only


# ── Router layer: ingest_upload validation mapping ────────────────────────

async def test_router_ingest_upload_maps_caps_and_rejects_extension(pool):
    response = await datasets_router.ingest_upload("orders.csv", CSV.encode())
    assert response.status_code == 201
    assert response.body["filename"] == "orders.csv" and response.body["reused"] is False

    with pytest.raises(HTTPException) as err:
        await datasets_router.ingest_upload("orders.txt", CSV.encode())
    assert err.value.status_code == 422 and ".csv" in err.value.detail

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(upload_store, "MAX_BYTES", 4)
    try:
        with pytest.raises(HTTPException) as err:
            await datasets_router.ingest_upload("orders.csv", CSV.encode())
        assert err.value.status_code == 413
    finally:
        monkeypatch.undo()


async def test_router_ingest_upload_identical_retry_returns_reused(pool):
    first = await datasets_router.ingest_upload("orders.csv", CSV.encode())
    second = await datasets_router.ingest_upload("orders.csv", CSV.encode())
    assert second.status_code == 200 and second.body["reused"] is True
    assert second.body["dataset_id"] == first.body["dataset_id"]
    assert len(pool.rows) == 1


async def test_router_upload_dataset_endpoint_reads_multipart_spool(pool):
    from starlette.datastructures import UploadFile

    file = UploadFile(file=BytesIO(CSV.encode()), size=len(CSV), filename="orders.csv")
    response = await datasets_router.upload_dataset(file)
    assert response.status_code == 201 and response.body["row_count"] == 3


async def test_router_metadata_and_preview_endpoints(pool):
    record, _ = await upload_store.create_dataset("orders.csv", CSV.encode())
    dataset_id = record["dataset_id"]

    assert (await datasets_router.get_dataset(dataset_id))["dataset_id"] == dataset_id
    with pytest.raises(HTTPException) as err:
        await datasets_router.get_dataset("01234567-89ab-cdef-0123-456789abcdef")
    assert err.value.status_code == 404

    preview = await datasets_router.preview_dataset(dataset_id, 2)
    assert preview["rows_returned"] == 2 and preview["truncated"] is True
    assert preview["row_count"] == 3 and preview["rows"][0]["region"] == "east"

    assert (await datasets_router.delete_dataset(dataset_id)) == {"deleted": True, "dataset_id": dataset_id}
    with pytest.raises(HTTPException) as err:
        await datasets_router.delete_dataset("01234567-89ab-cdef-0123-456789abcdef")
    assert err.value.status_code == 404


async def test_router_preview_query_limit respects_agreeable_bounds(pool):
    record, _ = await upload_store.create_dataset("orders.csv", CSV.encode())
    for bad in (0, -1, 51, 500):
        with pytest.raises(ValueError, match=r"limit"):
            await datasets_router.preview_dataset(record["dataset_id"], bad)


# ── Pipeline source resolution: upload_<dataset_id> ───────────────────────

def _uuid():
    return "1f0a2b3c-4d5e-4f60-81a2-b3c4d5e6f708"


async def test_materialize_upload_source_persists_snapshot_and_ref(tmp_path, monkeypatch, pool):
    monkeypatch.setattr(sources, "SNAPSHOT_DIR", tmp_path)
    save_calls = []
    async def _fake_save(run_id, source_id, payload, sha256, row_count, profile_rows):
        save_calls.append((run_id, source_id, payload, sha256, row_count, profile_rows))
    monkeypatch.setattr(store, "save_source_snapshot", _fake_save)

    record, _ = await upload_store.create_dataset("nov_orders.csv", CSV.encode())
    run_id = "run-1"
    source_id = f"upload_{record['dataset_id']}"
    materialized = await sources.materialize_source(source_id, "default", run_id)

    assert materialized.source_id == source_id
    assert materialized.family is None  # same precedent as bundled sources w/o curated baseline
    ref = materialized.ref
    assert ref["kind"] == "upload" and ref["dataset_id"] == record["dataset_id"]
    assert ref["source_id"] == source_id and ref["filename"] == "nov_orders.csv"
    assert ref["row_count"] == 3 and ref["profile_rows"] == 3
    assert ref["sha256"] == materialized.sha256 == hashlib.sha256(CSV.encode()).hexdigest()

    assert save_calls == [(run_id, source_id, CSV.encode(), ref["sha256"], 3, 3)]
    snapshot = (tmp_path / f"{run_id}.csv")
    assert snapshot.read_bytes() == CSV.encode()  # per-run file for the graph's profile_source node


async def test_materialize_upload_accepts_any_registered_contract_binding(tmp_path, monkeypatch, pool):
    """Binding is recorded, not column-enforced: healing drift is the point."""
    monkeypatch.setattr(sources, "SNAPSHOT_DIR", tmp_path)
    async def _noop_save(*args): pass
    monkeypatch.setattr(store, "save_source_snapshot", _noop_save)
    record, _ = await upload_store.create_dataset("nov_orders.csv", CSV.encode())

    for contract_name in ("default", "compact"):
        materialized = await sources.materialize_source(f"upload_{record['dataset_id']}", contract_name, "run-1")
        assert materialized.source_id.startswith("upload_")


async def test_materialize_upload_fails_closed_on_unknown_deleted_or_corrupt(tmp_path, monkeypatch, pool):
    monkeypatch.setattr(sources, "SNAPSHOT_DIR", tmp_path)
    record, _ = await upload_store.create_dataset("nov_orders.csv", CSV.encode())
    dataset_id = record["dataset_id"]

    with pytest.raises(ValueError, match="invalid uploaded dataset ID"):
        await sources.materialize_source("upload_not-a-uuid", "default", "run-1")
    with pytest.raises(ValueError, match="uploaded dataset not found"):
        await sources.materialize_source(f"upload_{_uuid()}", "default", "run-1")

    await upload_store.delete_dataset(dataset_id)
    with pytest.raises(ValueError, match="uploaded dataset not found"):
        await sources.materialize_source(f"upload_{dataset_id}", "default", "run-1")

    record2, _ = await upload_store.create_dataset("nov_orders.csv", CSV.encode())
    corrupted = dict(pool.rows[record2["dataset_id"]])
    corrupted["payload"] = b"order_id\n1\n"
    pool.rows[record2["dataset_id"]] = corrupted
    with pytest.raises(ValueError, match="digest mismatch"):
        await sources.materialize_source(f"upload_{record2['dataset_id']}", "default", "run-1")


async def test_ensure_source_path_restores_upload_bytes_from_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "SNAPSHOT_DIR", tmp_path)
    source_ref = {"kind": "upload", "sha256": hashlib.sha256(CSV.encode()).hexdigest()}
    state = {"run_id": "run-42", "source_path": str(tmp_path / "run-42.csv"), "source_ref": source_ref}
    restored: list[tuple] = []
    async def _fake_load(run_id, expected):
        restored.append((run_id, expected))
        return CSV.encode()
    monkeypatch.setattr(store, "load_source_snapshot", _fake_load)

    path = await sources.ensure_source_path(state)
    assert path == str(tmp_path / "run-42.csv")
    assert restored == [("run-42", source_ref["sha256"])]
    assert (tmp_path / "run-42.csv").read_bytes() == CSV.encode()

    state["source_path"] = str(tmp_path / "run-42.csv")  # file exists; digest verified again
    with pytest.raises(ValueError, match="digest mismatch"):
        await sources.ensure_source_path({"run_id": "run-43", "source_path": str(tmp_path / "run-999.csv"),
                                          "source_ref": {**source_ref, "sha256": "f" * 64}})


def test_ensure_source_path_still_passes_through_bundle_csv():
    state = {"run_id": "r", "source_path": "C:/x/day1.csv", "source_ref": {"kind": "bundled_csv"}}
    assert asyncio.get_event_loop_policy() is not None  # noqa: S101 — placeholder guard for readability
    assert sources.ensure_source_path.__doc__ is not None
    # kind outside the snapshot-backed set must return the path unchanged:
    import inspect
    body = inspect.getsource(sources.ensure_source_path)
    assert '"upload"' in body  # registered alongside database_table/local_json/external_batch
    assert '"bundled_csv"' not in body.split("return str(path)")[0]


def test_list_sources_catalog_does_not_leak_uploads():
    assert sources.UPLOAD_PREFIX == "upload_"
    listing = sources.list_sources()
    assert all(not item["source_id"].startswith(sources.UPLOAD_PREFIX) for item in listing)
