"""App contract upload/management — fully offline.

No Postgres, no network. The store's three SQL shapes are exercised through an
in-memory fake pool (same approach as test_p1's _MemoryPool), and every
validation case goes through the real pipeline.contract grammar so a
regression in `_validated_spec` is caught here too.

Covered per the feature brief:
  valid YAML, valid JSON, missing columns, invalid types, duplicate names,
  invalid constraints, historical snapshot compatibility, overlay/listing
  integration, YAML safety and router error mapping.
"""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from pipeline import contract as contract_module
from pipeline.contract import contract_for_state, contract_version, get_registry
from routers import config as config_router
from routers import contracts as contracts_router
from services import contract_store
from services.contract_store import (
    ContractConflict,
    ContractImmutable,
    ContractNotFound,
    ContractTooLarge,
    MAX_CONTRACT_BYTES,
)

pytestmark = pytest.mark.unit

VALID_YAML = b"""
columns:
  order_id:
    dtype: int64
    nullable: false
    unique: true
  region:
    dtype: str
    nullable: false
    allowed: [North, South]
  revenue:
    dtype: float64
    nullable: false
    min: 0
row_count_min: 5
"""

VALID_SPEC = {
    "columns": {
        "order_id": {"dtype": "int64", "nullable": False, "unique": True},
        "region": {"dtype": "str", "nullable": False, "allowed": ["North", "South"]},
        "revenue": {"dtype": "float64", "nullable": False, "min": 0},
    },
    "row_count_min": 5,
}


@pytest.fixture(autouse=True)
def _clean_overlay():
    contract_module.clear_uploaded_contracts()
    yield
    contract_module.clear_uploaded_contracts()


# ── Fake Postgres ───────────────────────────────────────────────────────────

class _Cursor:
    def __init__(self, row=None, rows=None):
        self._row, self._rows = row, list(rows or [])

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


def _unwrap(value):
    return getattr(value, "obj", value)


class _FakeContractDB:
    """Only the statements contract_store actually issues."""

    def __init__(self):
        self.contracts: dict[str, dict] = {}
        self.versions: dict[tuple[str, str], dict] = {}
        self.statements: list[str] = []
        self.clock = 0

    def _now(self):
        self.clock += 1
        return datetime(2026, 11, 3, 12, 0, self.clock, tzinfo=timezone.utc)

    @asynccontextmanager
    async def connection(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, statement, params=()):
        sql = " ".join(statement.lower().split())
        self.statements.append(sql)

        if sql.startswith("create"):
            return _Cursor()
        if sql.startswith("select 1 from app_contracts where name"):
            return _Cursor({"exists": 1} if params[0] in self.contracts else None)
        if sql.startswith("select name, version, spec, source, created_at, updated_at from app_contracts where name"):
            row = self.contracts.get(params[0])
            return _Cursor(dict(row) if row else None)
        if sql.startswith("select name, version, spec, source, created_at, updated_at from app_contracts order by name"):
            return _Cursor(rows=[dict(self.contracts[name]) for name in sorted(self.contracts)])
        if sql.startswith("select version, spec, source, created_at from app_contract_versions where name"):
            rows = [dict(row) for (name, _), row in self.versions.items() if name == params[0]]
            rows.sort(key=lambda row: row["created_at"], reverse=True)
            return _Cursor(rows=rows)
        if sql.startswith("select name, spec, version from app_contracts order by name"):
            return _Cursor(rows=[
                {"name": row["name"], "spec": row["spec"], "version": row["version"]}
                for row in (self.contracts[name] for name in sorted(self.contracts))
            ])
        if sql.startswith("insert into app_contracts"):
            name, spec, version = params
            timestamp = self._now()
            self.contracts[name] = {"name": name, "spec": _unwrap(spec), "version": version,
                                    "source": "upload", "created_at": timestamp,
                                    "updated_at": timestamp}
            return _Cursor()
        if sql.startswith("insert into app_contract_versions"):
            name, version, spec = params
            self.versions[(name, version)] = {"version": version, "spec": _unwrap(spec),
                                              "source": "upload", "created_at": self._now()}
            return _Cursor()
        if sql.startswith("delete from app_contracts where name"):
            name = params[0]
            if name not in self.contracts:
                return _Cursor()
            del self.contracts[name]
            for key in [key for key in self.versions if key[0] == name]:
                del self.versions[key]
            return _Cursor({"name": name})
        raise AssertionError(f"unexpected SQL: {sql}")


@pytest.fixture
def fake_db(monkeypatch):
    fake = _FakeContractDB()
    monkeypatch.setattr(contract_store, "get_pool", lambda: fake)
    return fake


class _Upload:
    def __init__(self, payload: bytes, filename: str = "contract.yaml"):
        self.payload, self.filename = payload, filename

    async def read(self, size: int = -1):
        return self.payload if size is None or size < 0 else self.payload[:size]


# ── Parsing and validation ──────────────────────────────────────────────────

def test_valid_yaml_parses_and_previews_every_constraint():
    parsed = contract_store.parse_contract_upload(VALID_YAML, "orders_v2")

    assert parsed.format == "yaml"
    assert parsed.name == "orders_v2"
    assert parsed.spec == VALID_SPEC
    assert parsed.version == contract_version(VALID_SPEC)
    assert parsed.columns == ["order_id", "region", "revenue"]
    assert parsed.column_count == 3

    preview = {row["name"]: row for row in contract_store.column_preview(parsed.spec)}
    assert preview["order_id"] == {"name": "order_id", "dtype": "int64", "nullable": False,
                                   "unique": True, "allowed": None, "min": None}
    assert preview["region"]["allowed"] == ["North", "South"]
    assert preview["region"]["unique"] is False
    assert preview["revenue"]["min"] == 0
    assert preview["revenue"]["nullable"] is False


def test_valid_json_upload_is_supported():
    payload = json.dumps({"columns": {"id": {"dtype": "int64"}}, "row_count_min": 0}).encode()
    parsed = contract_store.parse_contract_upload(payload, "json_contract")
    assert parsed.format == "json"
    assert parsed.spec["columns"] == {"id": {"dtype": "int64"}}
    assert contract_store.column_preview(parsed.spec)[0]["nullable"] is True


def test_missing_or_empty_columns_rejected():
    for payload in (b"row_count_min: 4", b"columns: {}", b"columns: []"):
        with pytest.raises(ValueError, match="nonempty columns"):
            contract_store.parse_contract_upload(payload)


def test_unsupported_dtype_rejected_with_existing_message():
    with pytest.raises(ValueError, match="unknown dtype for amount"):
        contract_store.parse_contract_upload(
            b"columns:\n  amount:\n    dtype: decimal\n"
        )


@pytest.mark.parametrize("payload, message", [
    (b"columns:\n  id:\n    dtype: int64\n    nullable: 'no'\n", "must be boolean"),
    (b"columns:\n  id:\n    dtype: int64\n    unique: 1\n", "must be boolean"),
    (b"columns:\n  id:\n    dtype: int64\n    allowed: [a, b]\n", "invalid allowed values"),
    (b"columns:\n  id:\n    dtype: str\n    allowed: [a, a]\n", "invalid allowed values"),
    (b"columns:\n  id:\n    dtype: str\n    allowed: []\n", "invalid allowed values"),
    (b"columns:\n  id:\n    dtype: int64\n    min: true\n", "invalid minimum"),
    (b"columns:\n  id:\n    dtype: float64\n    min: .nan\n", "invalid minimum"),
    (b"columns:\n  id:\n    dtype: int64\n    nope: 1\n", "invalid contract column spec"),
    (b"columns:\n  id:\n    dtype: int64\nrow_count_min: -1\n", "row_count_min"),
    (b"columns:\n  id:\n    dtype: int64\nextra: true\n", "invalid top-level keys"),
    (b"columns:\n  Bad Name:\n    dtype: int64\n", "invalid contract column"),
])
def test_invalid_schema_and_constraints_rejected(payload, message):
    with pytest.raises(ValueError, match=message):
        contract_store.parse_contract_upload(payload)


@pytest.mark.parametrize("name", ["Orders", "1orders", "../evil", "orders.v2", "order space", "a" * 65])
def test_invalid_contract_names_rejected(name):
    with pytest.raises(ValueError, match="contract name must match"):
        contract_store.parse_contract_upload(VALID_YAML, name)


def test_duplicate_keys_rejected_for_json_and_yaml():
    with pytest.raises(ValueError, match="duplicate contract key"):
        contract_store.parse_contract_upload(b'{"columns": {"id": {"dtype": "int64"}}, "columns": {}}')
    with pytest.raises(ValueError, match="duplicate contract key"):
        contract_store.parse_contract_upload(
            b"columns:\n  id: {dtype: int64}\n  id: {dtype: str}\n"
        )


def test_malformed_document_rejected():
    for payload in (b"columns: [unclosed", b"", b"\xff\xfe"):
        with pytest.raises(ValueError):
            contract_store.parse_contract_upload(payload)


def test_oversize_upload_rejected_before_parsing():
    with pytest.raises(ContractTooLarge):
        contract_store.parse_contract_upload(b"x" * (MAX_CONTRACT_BYTES + 1))


def test_yaml_python_object_tags_are_not_executed(tmp_path):
    payload = b"columns:\n  id: !!python/object/apply:os.system ['echo pwned']\n"
    with pytest.raises(ValueError, match="malformed contract document"):
        contract_store.parse_contract_upload(payload)
    assert list(tmp_path.iterdir()) == []


def test_oversized_numeric_minimum_rejected():
    huge = "9" * 400
    with pytest.raises(ValueError, match="invalid minimum"):
        contract_store.parse_contract_upload(
            f"columns:\n  id:\n    dtype: float64\n    min: {huge}\n".encode()
        )


def test_too_many_columns_rejected():
    columns = "\n".join(f"  col_{index}:\n    dtype: int64" for index in range(201))
    with pytest.raises(ValueError, match="too many columns"):
        contract_store.parse_contract_upload(f"columns:\n{columns}\n".encode())


# ── Persistence lifecycle ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schema_registration_listing_and_deletion(fake_db):
    await contract_store.ensure_schema()
    assert any("create table if not exists app_contracts" in sql for sql in fake_db.statements)
    assert any("create table if not exists app_contract_versions" in sql for sql in fake_db.statements)

    parsed = contract_store.parse_contract_upload(VALID_YAML, "orders_v2")
    stored = await contract_store.register_contract(parsed, "orders_v2")

    assert stored["name"] == "orders_v2"
    assert stored["version"] == parsed.version
    assert stored["source"] == "upload"
    assert stored["versions"][0]["current"] is True
    assert fake_db.versions[("orders_v2", parsed.version)]["spec"] == VALID_SPEC

    listed = await contract_store.list_contracts()
    assert [row["name"] for row in listed] == ["orders_v2"]
    assert listed[0]["columns"] == ["order_id", "region", "revenue"]
    assert listed[0]["deletable"] is True

    got = await contract_store.get_contract("orders_v2")
    assert got["versions"][0]["version"] == parsed.version
    assert await contract_store.get_contract("missing") is None

    await contract_store.delete_contract("orders_v2")
    assert await contract_store.get_contract("orders_v2") is None
    with pytest.raises(ContractNotFound):
        await contract_store.delete_contract("orders_v2")


@pytest.mark.asyncio
async def test_duplicate_names_are_rejected(fake_db):
    parsed = contract_store.parse_contract_upload(VALID_YAML, "orders_v2")
    await contract_store.register_contract(parsed, "orders_v2")
    with pytest.raises(ContractConflict, match="already exists"):
        await contract_store.register_contract(parsed, "orders_v2")

    # File contracts are reserved too — an upload may never shadow `default`.
    with pytest.raises(ContractConflict, match="already exists"):
        await contract_store.register_contract(parsed, "default")


@pytest.mark.asyncio
async def test_file_contracts_are_immutable():
    with pytest.raises(ContractImmutable, match="immutable"):
        await contract_store.delete_contract("default")
    with pytest.raises(ContractImmutable, match="immutable"):
        await contract_store.delete_contract("compact")


# ── Overlay and compatibility with existing callers ─────────────────────────

@pytest.mark.asyncio
async def test_overlay_feeds_registry_and_existing_config_listing(fake_db):
    parsed = contract_store.parse_contract_upload(VALID_YAML, "orders_v2")
    await contract_store.register_contract(parsed, "orders_v2")

    registry = get_registry()
    assert "orders_v2" in registry.names()
    resolved = registry.get("orders_v2")
    assert resolved.version == parsed.version
    assert resolved.expected_dtypes == {"order_id": "int64", "region": "str", "revenue": "float64"}

    # The pre-existing GET /api/config/contracts implementation is unchanged
    # and must now include the upload with exactly its old shape.
    listing = await config_router.list_contracts()
    by_name = {row["name"]: row for row in listing["contracts"]}
    assert set(by_name) == {"default", "compact", "orders_v2"}
    assert by_name["orders_v2"] == {
        "name": "orders_v2",
        "version": parsed.version,
        "columns": ["order_id", "region", "revenue"],
    }
    assert by_name["default"]["version"] == get_registry().get("default").version
    assert set(by_name["default"]) == {"name", "version", "columns"}


@pytest.mark.asyncio
async def test_historical_snapshot_still_validates_after_overlay_changes(fake_db):
    parsed = contract_store.parse_contract_upload(VALID_YAML, "orders_v2")
    await contract_store.register_contract(parsed, "orders_v2")

    historical = {
        "contract_name": "orders_v2",
        "contract_version": parsed.version,
        "contract_snapshot": parsed.spec,
    }
    # Simulates a run resumed in a fresh process after the contract was
    # deleted: the checkpointed snapshot, not the registry, is authoritative.
    contract_module.clear_uploaded_contracts()
    await contract_store.delete_contract("orders_v2")

    rebuilt = contract_for_state(historical)
    assert rebuilt.name == "orders_v2"
    assert rebuilt.version == parsed.version
    assert rebuilt.expected_dtypes == {"order_id": "int64", "region": "str", "revenue": "float64"}

    with pytest.raises(ValueError, match="hash mismatch"):
        contract_for_state({**historical, "contract_version": "0" * 64})


@pytest.mark.asyncio
async def test_load_into_registry_rejects_tampered_hash(fake_db):
    parsed = contract_store.parse_contract_upload(VALID_YAML, "orders_v2")
    await contract_store.register_contract(parsed, "orders_v2")
    fake_db.contracts["orders_v2"]["version"] = "0" * 64
    contract_module.clear_uploaded_contracts()
    with pytest.raises(ValueError, match="hash mismatch"):
        await contract_store.load_into_registry()


# ── Router-facing behaviour (functions called directly, FastAPI handles HTTP) ─

@pytest.mark.asyncio
async def test_preview_reports_availability_and_validation_errors(fake_db):
    preview = await contracts_router.preview_contract(file=_Upload(VALID_YAML), name="orders_v2")
    assert preview["name"] == "orders_v2"
    assert preview["name_available"] is True
    assert preview["format"] == "yaml"
    assert preview["columns"] == ["order_id", "region", "revenue"]
    assert preview["column_preview"][1]["allowed"] == ["North", "South"]

    reserved = await contracts_router.preview_contract(file=_Upload(VALID_YAML), name="default")
    assert reserved["name_available"] is False

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await contracts_router.preview_contract(
            file=_Upload(b"columns:\n  id:\n    dtype: nope\n"), name="bad"
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_register_and_delete_router_shapes(fake_db):
    from fastapi import HTTPException

    detail = await contracts_router.register_contract(name="orders_v2", file=_Upload(VALID_YAML))
    assert detail["deletable"] is True
    assert detail["source"] == "upload"
    assert detail["columns"] == ["order_id", "region", "revenue"]
    assert detail["versions"][0]["current"] is True
    assert isinstance(detail["created_at"], str)

    listed = await contracts_router.list_contracts()
    row = next(item for item in listed["contracts"] if item["name"] == "orders_v2")
    assert row["deletable"] is True and row["column_count"] == 3
    assert next(item for item in listed["contracts"] if item["name"] == "default")["deletable"] is False

    with pytest.raises(HTTPException) as conflict:
        await contracts_router.register_contract(name="orders_v2", file=_Upload(VALID_YAML))
    assert conflict.value.status_code == 409

    with pytest.raises(HTTPException) as immutable:
        await contracts_router.delete_contract("default")
    assert immutable.value.status_code == 403

    with pytest.raises(HTTPException) as missing:
        await contracts_router.get_contract("nope")
    assert missing.value.status_code == 404

    assert await contracts_router.delete_contract("orders_v2") == {
        "deleted": True, "name": "orders_v2"
    }


@pytest.mark.asyncio
async def test_router_413_for_oversize_and_422_for_bad_name(fake_db):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as too_large:
        await contracts_router.register_contract(
            name="orders_v2", file=_Upload(b"x" * (MAX_CONTRACT_BYTES + 1))
        )
    assert too_large.value.status_code == 413

    with pytest.raises(HTTPException) as bad_name:
        await contracts_router.register_contract(name="Bad Name", file=_Upload(VALID_YAML))
    assert bad_name.value.status_code == 422
