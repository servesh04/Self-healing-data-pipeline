"""Versioned object identity, bounded reads and external orchestration gates."""

from io import BytesIO
from pathlib import Path
import ast
from types import SimpleNamespace
import hashlib

import pytest

from scripts import object_orders_feed as feed
from scripts.orders_loader import stage as stage_csv, publish
from test_p4 import Connection, _manifest, _authorize
from pipeline.mapping import MappingPatch

DATA = Path(__file__).resolve().parents[1] / "data" / "sources"


class Body(BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.was_closed = False
    def close(self):
        self.was_closed = True
        super().close()


class ObjectClient:
    def __init__(self, payload, version="v1", length=None):
        self.body = Body(payload)
        self.version = version
        self.length = len(payload) if length is None else length
        self.calls = []
    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        return {"Body": self.body, "VersionId": self.version,
                "ContentLength": self.length}


@pytest.fixture(autouse=True)
def bucket(monkeypatch):
    monkeypatch.setenv("P6_ORDERS_BUCKET", "orders-p6-test")


def test_object_identity_is_stable_and_version_changes_batch():
    a = feed.batch_id_for_object("partner_orders_a", "orders-p6-test", "incoming/partner_orders_a/x.csv", "v1")
    b = feed.batch_id_for_object("partner_orders_a", "orders-p6-test", "incoming/partner_orders_a/x.csv", "v2")
    assert a == feed.batch_id_for_object("partner_orders_a", "orders-p6-test", "incoming/partner_orders_a/x.csv", "v1")
    assert a != b and len(a) == 64 and a.startswith("obj_")


@pytest.mark.parametrize("feed_id,key,version", [
    ("unknown", "incoming/partner_orders_a/x.csv", "v1"),
    ("partner_orders_a", "incoming/partner_orders_b/x.csv", "v1"),
    ("partner_orders_a", "incoming/partner_orders_a/../x.csv", "v1"),
    ("partner_orders_a", "incoming/partner_orders_a/evil\\x.csv", "v1"),
    ("partner_orders_a", "incoming/partner_orders_a/evil\nx.csv", "v1"),
    ("partner_orders_a", "incoming/partner_orders_a/" + "x" * 1024 + ".csv", "v1"),
    ("partner_orders_a", "https://evil.example/x.csv", "v1"),
    ("partner_orders_a", "incoming/partner_orders_a/x.csv", "null"),
    ("partner_orders_a", "incoming/partner_orders_a/x.csv", ""),
])
def test_unknown_or_mutable_object_identity_rejected(feed_id, key, version):
    with pytest.raises(ValueError):
        feed.object_spec(feed_id, key, version)


def test_get_object_requests_exact_version_and_bounded_complete_bytes():
    payload = (DATA / "day1_clean.csv").read_bytes()
    client = ObjectClient(payload)
    received, spec = feed.fetch_versioned_object("partner_orders_a",
        "incoming/partner_orders_a/day1.csv", "v1", client)
    assert received == payload and spec["bucket"] == "orders-p6-test"
    assert client.calls == [{"Bucket": "orders-p6-test",
                             "Key": "incoming/partner_orders_a/day1.csv", "VersionId": "v1"}]
    assert client.body.was_closed


def test_version_mismatch_and_oversize_fail_closed(monkeypatch):
    path = "incoming/partner_orders_a/day1.csv"
    wrong = ObjectClient(b"abc", version="v2")
    with pytest.raises(ValueError, match="requested immutable version"):
        feed.fetch_versioned_object("partner_orders_a", path, "v1", wrong)
    assert wrong.body.was_closed
    monkeypatch.setattr(feed, "MAX_BYTES", 10)
    oversized = ObjectClient(b"x" * 11, length=10)  # dishonest ContentLength
    with pytest.raises(ValueError, match="byte cap"):
        feed.fetch_versioned_object("partner_orders_a", path, "v1", oversized)
    assert oversized.body.was_closed


def test_staged_object_binds_version_and_digest(monkeypatch):
    payload = (DATA / "day1_clean.csv").read_bytes()
    calls = []
    def stage(conn, feed_id, batch_id, data, provenance):
        calls.append((conn, feed_id, batch_id, data, provenance))
        return {"sha256": hashlib.sha256(data).hexdigest(), "row_count": 50}
    monkeypatch.setattr(feed, "stage", stage)
    result = feed.stage_object(object(), "partner_orders_a", "incoming/partner_orders_a/a.csv",
                               "v1", ObjectClient(payload))
    assert result["batch_id"] == calls[0][2]
    assert calls[0][3] == payload and calls[0][4]["version_id"] == "v1"
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()


def test_retry_uses_saved_stage_and_rejects_altered_provenance():
    payload = (DATA / "day1_clean.csv").read_bytes()
    key = "incoming/partner_orders_a/a.csv"
    spec = feed.object_spec("partner_orders_a", key, "v1")
    expected_batch = feed.batch_id_for_object("partner_orders_a", spec["bucket"], key, "v1")
    row = {"payload": payload, "sha256": hashlib.sha256(payload).hexdigest(),
           "row_count": 50, "object_bucket": spec["bucket"],
           "object_key": key, "object_version_id": "v1"}
    class Conn:
        def execute(self, sql, params):
            assert params == ("partner_orders_a", expected_batch)
            return type("Cursor", (), {"fetchone": lambda self: row})()
    assert feed.saved_object_stage(Conn(), "partner_orders_a", key, "v1")["sha256"] == row["sha256"]
    class NoGet:
        def get_object(self, **_): raise AssertionError("retry fetched object again")
    assert feed.stage_or_resume_object(Conn(), "partner_orders_a", key, "v1", NoGet())["sha256"] == row["sha256"]
    row["object_version_id"] = "v2"
    with pytest.raises(ValueError, match="identity"):
        feed.saved_object_stage(Conn(), "partner_orders_a", key, "v1")


def test_object_stage_flows_into_independent_loader_and_is_immutable():
    payload = (DATA / "day1_clean.csv").read_bytes()
    conn = Connection()
    key = "incoming/partner_orders_a/control.csv"
    first = feed.stage_object(conn, "partner_orders_a", key, "v1", ObjectClient(payload))
    second = feed.stage_object(conn, "partner_orders_a", key, "v1", ObjectClient(payload))
    assert first == second
    row = conn.staged[("partner_orders_a", first["batch_id"])]
    assert (row["object_bucket"], row["object_key"], row["object_version_id"]) == (
        "orders-p6-test", key, "v1")
    with pytest.raises(ValueError, match="identity"):
        stage_csv(conn, "partner_orders_a", first["batch_id"], payload)
    mapping = MappingPatch().model_dump()
    manifest = _manifest(conn, "partner_orders_a", first["batch_id"], 73, mapping)
    _authorize(conn, manifest)
    assert publish(conn, "partner_orders_a", first["batch_id"], manifest) == {
        "published": True, "row_count": 50}
    assert not publish(conn, "partner_orders_a", first["batch_id"], manifest)["published"]


class Response:
    def __init__(self, data, code=200): self.data, self.status_code = data, code
    def raise_for_status(self):
        if self.status_code >= 400: raise ValueError(f"HTTP {self.status_code}")
    def json(self): return self.data


class Agent:
    def __init__(self, status="running", run="run-one", manifest=None):
        self.status, self.run, self.manifest = status, run, manifest
        self.posts = []
    def post(self, url):
        self.posts.append(url)
        if url.endswith("/trigger"): return Response({"run_id": self.run})
        return Response(self.manifest)
    def get(self, url):
        return Response({"run_id": self.run, "api_status": self.status})


def test_object_flow_waits_for_approval_and_blocks_bad_status(monkeypatch):
    staged = {"feed_id": "partner_orders_a", "batch_id": "obj_test", "sha256": "digest"}
    agent = Agent()
    run_id = feed.trigger_agent(agent, staged)
    assert run_id == "run-one" and feed.batch_status(agent, staged, run_id) == "running"
    agent.status = "awaiting_approval"
    with pytest.raises(ValueError, match="awaiting_approval"):
        feed.publish_batch(object(), agent, staged, run_id)
    assert agent.posts == ["/api/external/orders/partner_orders_a/obj_test/trigger"]
    for blocked in ("alert", "escalated"):
        agent.status = blocked
        with pytest.raises(ValueError, match=blocked):
            feed.publish_batch(object(), agent, staged, run_id)
    agent.status, agent.run = "healed", "wrong-run"
    with pytest.raises(ValueError, match="run ID differs"):
        feed.batch_status(agent, staged, run_id)


def test_healed_release_publishes_exact_stage_once(monkeypatch):
    staged = {"feed_id": "partner_orders_a", "batch_id": "obj_test", "sha256": "digest"}
    agent = Agent(status="healed", manifest={"run_id": "run-one", "sha256": "digest"})
    class Conn:
        commits = 0
        def commit(self): self.commits += 1
    conn = Conn()
    calls = []
    def publish(_conn, feed_id, batch_id, manifest):
        calls.append((feed_id, batch_id, manifest))
        return {"published": len(calls) == 1, "row_count": 50}
    monkeypatch.setattr(feed, "publish", publish)
    assert feed.publish_batch(conn, agent, staged, "run-one")["published"]
    assert not feed.publish_batch(conn, agent, staged, "run-one")["published"]
    assert len(calls) == 2 and conn.commits == 2
    agent.manifest = {"run_id": "run-one", "sha256": "altered"}
    with pytest.raises(ValueError, match="digest"):
        feed.publish_batch(conn, agent, staged, "run-one")
    assert len(calls) == 2


def test_reference_dag_has_reschedule_sensor_and_publish_dependency():
    source = (Path(__file__).resolve().parents[1] / "airflow_dags" / "orders_object_feed.py").read_text()
    tree = ast.parse(source)
    assert 'mode="reschedule"' in source
    assert "P5_APPROVER_TOKEN" not in source
    functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert {"stage_exact_version", "trigger", "wait_for_validation", "publish_validated"} <= functions
    assert "ready = wait_for_validation(staged, run_id)" in source
    assert "publish_validated(staged, run_id, ready)" in source


def test_reference_dag_parses_with_stub_airflow_sdk(monkeypatch):
    import importlib.util
    import sys
    import types
    seen = {}
    class XCom:
        def __init__(self, name, args): self.name, self.args = name, args
    def task_decorator(*decorator_args, **options):
        def decorate(fn):
            def schedule(*args):
                seen[fn.__name__] = {"args": args, "options": options}
                return XCom(fn.__name__, args)
            return schedule
        return decorate(decorator_args[0]) if decorator_args and callable(decorator_args[0]) else decorate
    task_decorator.sensor = task_decorator
    sdk = types.ModuleType("airflow.sdk")
    sdk.PokeReturnValue = type("PokeReturnValue", (), {})
    sdk.task = task_decorator
    sdk.get_current_context = lambda: None
    sdk.dag = lambda **_kwargs: lambda fn: fn
    monkeypatch.setitem(sys.modules, "airflow", types.ModuleType("airflow"))
    monkeypatch.setitem(sys.modules, "airflow.sdk", sdk)
    path = Path(__file__).resolve().parents[1] / "airflow_dags" / "orders_object_feed.py"
    spec = importlib.util.spec_from_file_location("p6_reference_dag_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert seen["wait_for_validation"]["options"]["mode"] == "reschedule"
    assert seen["trigger"]["args"][0].name == "stage_exact_version"
    assert seen["publish_validated"]["args"][2].name == "wait_for_validation"


@pytest.mark.asyncio
async def test_object_columns_migrate_offline_and_runtime_checks_them(monkeypatch):
    from services import external_store, store
    queries = []
    class Cursor:
        def __init__(self, rows): self.rows = rows
        async def fetchone(self): return self.rows[0]
        async def fetchall(self): return self.rows
    class Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def execute(self, sql, params=()):
            queries.append(" ".join(sql.split()).lower())
            if "to_regclass" in sql:
                return Cursor([{"relation": params[0]}])
            if "information_schema.columns" in sql:
                return Cursor([{"column_name": name} for name in
                               ("object_bucket", "object_key", "object_version_id")])
            return Cursor([{}])
    pool = SimpleNamespace(connection=lambda: Conn())
    monkeypatch.setattr(external_store, "get_pool", lambda: pool)
    monkeypatch.setattr(store, "get_pool", lambda: pool)
    await external_store.init_external_tables()
    assert any("add column if not exists object_version_id" in query for query in queries)
    assert any("external_order_object_complete" in query for query in queries)
    assert any("old.object_version_id is distinct from new.object_version_id" in query for query in queries)
    queries.clear()
    await store.verify_runtime_schema()
    assert any("information_schema.columns" in query for query in queries)
