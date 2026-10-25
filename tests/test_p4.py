"""External loader gate, exact-batch retry, feed scope and destination safety."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import hashlib
import asyncio
import json

import pytest
from fastapi import BackgroundTasks, HTTPException
from langgraph.types import Command

from integrations.orders import feed_scope, feed_spec, validate_batch_id
from pipeline.contract import load_contract
from pipeline.mapping import MappingPatch
from pipeline.runner import run_pipeline
from scripts.orders_loader import publish, stage, validate_for_publish, SUPPORTED_CONTRACT_VERSION
from routers import external
from test_p0_graph import graph_fixture, run_graph

DATA = Path(__file__).resolve().parents[1] / "data" / "sources"


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    """Transactional test double for the loader's narrow SQL contract."""
    def __init__(self):
        self.staged = {}
        self.published = {}
        self.releases = {}
        self.orders = []
        self.run_records = {}
        self.versions = {}

    def transaction(self):
        conn = self
        class Transaction:
            def __enter__(self):
                self.before = deepcopy((conn.staged, conn.published, conn.orders, conn.releases))
            def __exit__(self, typ, *_):
                if typ:
                    conn.staged, conn.published, conn.orders, conn.releases = self.before
        return Transaction()

    def execute(self, query, params=()):
        query = " ".join(query.split()).lower()
        if "pg_advisory_xact_lock" in query:
            return Cursor()
        if query.startswith("insert into external_order_batches"):
            feed, batch, payload, digest, count = params
            self.staged.setdefault((feed, batch), {"payload": payload, "sha256": digest,
                                                   "row_count": count, "agent_run_id": None})
            return Cursor()
        if "from external_order_batches" in query:
            return Cursor(self.staged.get(tuple(params[:2])))
        if "from run_records" in query:
            return Cursor(self.run_records.get(params[0]))
        if "from mapping_state" in query:
            version, env = params[:2]
            mapping = self.versions.get((env, version))
            return Cursor({"mapping": mapping, "rolled_back": mapping == "rolled_back"} if mapping else None)
        if "from external_order_releases" in query:
            return Cursor(self.releases.get(tuple(params[:2])))
        if "from published_order_batches" in query:
            return Cursor(self.published.get(tuple(params[:2])))
        if "count(*)" in query and "from published_orders" in query:
            return Cursor({"n": sum(row[:2] == tuple(params[:2]) for row in self.orders)})
        if query.startswith("insert into published_order_batches"):
            feed, batch, digest, run_id, version, count = params
            self.published[(feed, batch)] = {"sha256": digest, "agent_run_id": run_id,
                                             "mapping_version": version, "row_count": count}
            return Cursor()
        raise AssertionError(f"unexpected SQL: {query}")

    def cursor(self):
        conn = self
        class BatchCursor:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def executemany(self, query, rows):
                assert "insert into published_orders" in query
                conn.orders.extend(rows)
        return BatchCursor()


def _manifest(conn, feed, batch, version, mapping):
    staged = conn.staged[(feed, batch)]
    env = feed_scope("p4-test", feed)
    conn.versions[(env, version)] = mapping
    conn.run_records["agent-run"] = {"pipeline_env": env, "contract_name": "default",
                                     "source_ref": {"sha256": staged["sha256"],
                                                    "feed_id": feed, "batch_id": batch}}
    staged["agent_run_id"] = "agent-run"
    return {"feed_id": feed, "batch_id": batch, "sha256": staged["sha256"],
            "run_id": "agent-run", "pipeline_env": env, "mapping_version": version,
            "mapping": mapping, "row_count": staged["row_count"],
            "contract_version": SUPPORTED_CONTRACT_VERSION}


def _authorize(conn, manifest):
    conn.releases[(manifest["feed_id"], manifest["batch_id"])] = {
        "sha256": manifest["sha256"], "agent_run_id": manifest["run_id"],
        "pipeline_env": manifest["pipeline_env"], "mapping_version": manifest["mapping_version"],
        "mapping_sha256": hashlib.sha256(json.dumps(manifest["mapping"], sort_keys=True,
                                                   separators=(",", ":")).encode()).hexdigest(),
        "row_count": manifest["row_count"], "contract_version": manifest["contract_version"]}


def test_approved_rename_retries_same_batch_into_distinct_destination():
    payload = (DATA / "day6_ambiguous_rename.csv").read_bytes()
    assert not run_pipeline(str(DATA / "day6_ambiguous_rename.csv"), MappingPatch(), load_contract()).passed
    with pytest.raises(ValueError, match="columns"):
        validate_for_publish(payload, MappingPatch().model_dump())
    conn = Connection()
    staged = stage(conn, "partner_orders_a", "batch_one", payload)
    assert stage(conn, "partner_orders_a", "batch_one", payload) == staged
    with pytest.raises(ValueError, match="different bytes"):
        stage(conn, "partner_orders_a", "batch_one", (DATA / "day1_clean.csv").read_bytes())
    # Simulates the agent's human-approved full mapping snapshot and exact
    # release manifest after rerun validation, not a caller-supplied patch.
    mapping = MappingPatch(renames={"acct_ref": "customer_id"}).model_dump()
    manifest = _manifest(conn, "partner_orders_a", "batch_one", 42, mapping)
    _authorize(conn, manifest)
    assert publish(conn, "partner_orders_a", "batch_one", manifest) == {"published": True, "row_count": 50}
    assert len(conn.orders) == 50
    assert conn.orders[0][0:2] == ("partner_orders_a", "batch_one")
    assert publish(conn, "partner_orders_a", "batch_one", manifest) == {"published": False, "row_count": 50}
    assert len(conn.orders) == 50


def test_publish_blocks_bad_digest_rollback_and_invalid_output():
    conn = Connection()
    payload = (DATA / "day6_ambiguous_rename.csv").read_bytes()
    stage(conn, "partner_orders_a", "batch_two", payload)
    manifest = _manifest(conn, "partner_orders_a", "batch_two", 43,
                         MappingPatch(renames={"acct_ref": "customer_id"}).model_dump())
    _authorize(conn, manifest)
    bad = {**manifest, "sha256": "0" * 64}
    with pytest.raises(ValueError, match="staged bytes"):
        publish(conn, "partner_orders_a", "batch_two", bad)
    conn.versions[(manifest["pipeline_env"], 43)] = "rolled_back"
    with pytest.raises(ValueError, match="rolled back"):
        publish(conn, "partner_orders_a", "batch_two", manifest)
    conn.versions[(manifest["pipeline_env"], 43)] = manifest["mapping"]
    forged = {**manifest, "mapping": MappingPatch(renames={"acct_ref": "order_id"}).model_dump()}
    with pytest.raises(ValueError, match="not authorized"):
        publish(conn, "partner_orders_a", "batch_two", forged)
    conn.versions[(manifest["pipeline_env"], 43)] = MappingPatch().model_dump()
    with pytest.raises(ValueError, match="mapping version"):
        publish(conn, "partner_orders_a", "batch_two", manifest)
    conn.versions[(manifest["pipeline_env"], 43)] = manifest["mapping"]
    with pytest.raises(ValueError, match="not authorized"):
        publish(conn, "partner_orders_a", "batch_two", {**manifest, "mapping": MappingPatch().model_dump()})
    assert conn.orders == [] and conn.published == {}


def test_feed_namespaces_and_unknown_ids_are_isolated():
    assert feed_spec("partner_orders_a")["contract_name"] == "default"
    assert feed_scope("p4-test", "partner_orders_a") != feed_scope("p4-test", "partner_orders_b")
    with pytest.raises(ValueError, match="unknown external feed"):
        feed_spec("unlisted_feed")
    with pytest.raises(ValueError, match="invalid external batch"):
        validate_batch_id("../bad")


@pytest.mark.parametrize("filename,patch", [
    ("day1_clean.csv", {}),
    ("day2_renamed.csv", {"renames": {"cust_id": "customer_id"}}),
    ("day3_type_drift.csv", {"casts": {"revenue": "strip_commas_to_float"}}),
    ("day4_combo.csv", {"renames": {"cust_id": "customer_id"},
                         "casts": {"revenue": "strip_commas_to_float"}}),
    ("day7_value_drift.csv", {"value_maps": {"region": {"N": "North", "S": "South",
                                                      "E": "East", "W": "West"}}}),
    ("day9_format_dmy.csv", {"date_parse": {"order_date": "dmy_slash_utc"}}),
])
def test_external_validator_matches_agent_contract_on_supported_repairs(filename, patch):
    mapping = MappingPatch.model_validate(patch)
    path = DATA / filename
    assert run_pipeline(str(path), mapping, load_contract()).passed
    rows = validate_for_publish(path.read_bytes(), mapping.model_dump())
    assert len(rows) == len(path.read_text().splitlines()) - 1


def test_external_validator_rejects_noninteger_key_even_if_digits_look_numeric():
    payload = (DATA / "day1_clean.csv").read_text().replace("\n1,1000,", "\n1.0,1000,", 1).encode()
    with pytest.raises(ValueError, match="invalid numeric"):
        validate_for_publish(payload, MappingPatch().model_dump())


def test_loader_contract_version_is_pinned_to_reviewed_agent_contract():
    assert SUPPORTED_CONTRACT_VERSION == load_contract().version


def test_graph_approval_then_external_loader_retry(monkeypatch):
    """Actual graph pause/resume mapping flows into the independent loader."""
    graph, memory = graph_fixture(monkeypatch)
    paused, cfg = run_graph(graph, "day6_ambiguous_rename.csv")
    assert paused["assertions_passed"] is False
    assert paused["mapping_version_written"] is None and memory.writes == 0
    payload = (DATA / "day6_ambiguous_rename.csv").read_bytes()
    conn = Connection()
    stage(conn, "partner_orders_a", "batch_graph", payload)
    with pytest.raises(ValueError, match="columns"):
        validate_for_publish(payload, MappingPatch().model_dump())
    healed = asyncio.run(graph.ainvoke(Command(resume={"decision": "approve"}), config=cfg),
                         loop_factory=asyncio.ProactorEventLoop)
    assert healed["status"] == "healed" and memory.writes == 1
    manifest = _manifest(conn, "partner_orders_a", "batch_graph", memory.version,
                         memory.mapping.model_dump())
    _authorize(conn, manifest)
    assert publish(conn, "partner_orders_a", "batch_graph", manifest)["row_count"] == 50
    assert len(conn.orders) == 50


@pytest.mark.asyncio
async def test_bridge_trigger_is_idempotent_and_feed_scoped(monkeypatch, tmp_path):
    payload = (DATA / "day1_clean.csv").read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    claimed = []
    async def noop(): pass
    async def prepare(feed, batch, proposed, env, contract, version, path):
        claimed.append((feed, batch, env))
        return {"run_id": "existing-run", "created": len(claimed) == 1,
                "payload": payload, "sha256": digest, "row_count": 50}
    values = {}
    class Graph:
        async def aget_state(self, *_): return SimpleNamespace(values=values)
    monkeypatch.setattr(external.external_store, "init_external_tables", noop)
    monkeypatch.setattr(external.external_store, "prepare_run", prepare)
    dispatch_calls = []
    async def dispatch(*args):
        dispatch_calls.append(args)
        return len(dispatch_calls) == 1
    monkeypatch.setattr(external.external_store, "claim_dispatch", dispatch)
    monkeypatch.setattr(external, "get_settings", lambda: SimpleNamespace(pipeline_env="p4-test", auth_mode="demo"))
    monkeypatch.setattr(external, "SNAPSHOT_DIR", tmp_path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph_app=Graph())))
    background = BackgroundTasks()
    first = await external.trigger_external_batch("partner_orders_a", "batch_one", background, request)
    assert first["run_id"] == "existing-run" and len(background.tasks) == 1
    assert claimed[0][2] == "p4-test:external:partner_orders_a"
    assert (tmp_path / "existing-run.csv").read_bytes() == payload
    # A duplicate request can arrive before the first graph checkpoint.
    # The durable dispatch claim, not a checkpoint race, blocks scheduling.
    racing_background = BackgroundTasks()
    await external.trigger_external_batch("partner_orders_a", "batch_one", racing_background, request)
    assert len(racing_background.tasks) == 0
    values["status"] = "running"
    second = await external.trigger_external_batch("partner_orders_a", "batch_one", BackgroundTasks(), request)
    assert second["already_registered"] is True
    assert len(background.tasks) == 1
    with pytest.raises(HTTPException) as exc:
        await external.trigger_external_batch("unknown", "batch_one", BackgroundTasks(), request)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_release_requires_terminal_validated_pinned_run(monkeypatch):
    digest = "a" * 64
    env = "p4-test:external:partner_orders_a"
    state = {"status": "healed", "assertions_passed": True,
             "source_sha256": digest, "mapping_version_written": 44,
             "contract_snapshot": load_contract().spec, "contract_name": "default"}
    class Graph:
        async def aget_state(self, *_): return SimpleNamespace(values=state, next=())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph_app=Graph())))
    async def batch(*_): return {"agent_run_id": "run", "sha256": digest, "row_count": 50}
    async def record(*_):
        return {"pipeline_env": env, "contract_name": "default",
                "source_ref": {"kind": "external_batch", "feed_id": "partner_orders_a",
                               "batch_id": "batch_one", "sha256": digest}}
    survived = {"yes": True}
    async def valid(*_): return survived["yes"]
    async def mapping(*_): return MappingPatch(renames={"acct_ref": "customer_id"})
    monkeypatch.setattr(external.external_store, "get_batch", batch)
    monkeypatch.setattr(external.external_store, "version_survived_rollback", valid)
    async def authorize(*_): pass
    monkeypatch.setattr(external.external_store, "authorize_release", authorize)
    monkeypatch.setattr(external.store, "get_run_record", record)
    monkeypatch.setattr(external.store, "get_mapping_by_version", mapping)
    monkeypatch.setattr(external, "get_settings", lambda: SimpleNamespace(pipeline_env="p4-test"))
    manifest = await external.release_external_batch("partner_orders_a", "batch_one", request)
    assert manifest["mapping_version"] == 44 and manifest["pipeline_env"] == env
    state["status"] = "awaiting_approval"
    with pytest.raises(HTTPException) as exc:
        await external.release_external_batch("partner_orders_a", "batch_one", request)
    assert exc.value.status_code == 409
    state["status"] = "healed"
    survived["yes"] = False
    with pytest.raises(HTTPException, match="rolled back"):
        await external.release_external_batch("partner_orders_a", "batch_one", request)
