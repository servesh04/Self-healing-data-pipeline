"""P2 alert, bounded source, and reviewed-context regression gates."""

import asyncio
import csv
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from graph.nodes.commit_report import commit_report
from pipeline.contract import load_contract
from pipeline.distribution import check_distribution, load_baseline
from pipeline.mapping import MappingPatch
from pipeline.runner import run_pipeline
from pipeline import sources
from routers.runs import TriggerRunRequest, _blank_state, trigger_run
from services import approval_examples

DATA = Path(__file__).resolve().parents[1] / "data" / "sources"


@pytest.mark.parametrize("filename,expected_status", [
    ("day8_revenue_shift.csv", "alert"),
    ("day8_normal_control.csv", "clean"),
    ("day8_low_sample.csv", "clean"),
])
def test_distribution_alert_is_separate_from_schema_repair(filename, expected_status):
    path = DATA / filename
    contract = load_contract()
    assert run_pipeline(str(path), MappingPatch(), contract).passed
    pinned = load_baseline("default", "orders")
    result = check_distribution(str(path), MappingPatch(), "default", "orders", pinned_baseline=pinned)
    report = commit_report({"heal_attempts": 0, "distribution_alerts": result.alerts})
    assert report["status"] == expected_status
    assert result.baseline_version == "orders-revenue-v1"
    if filename == "day8_revenue_shift.csv":
        assert result.alerts[0]["column"] == "revenue"
    if filename == "day8_low_sample.csv":
        assert result.alerts == []
        assert result.skipped[0]["reason"] == "insufficient sample"
        assert result.profile["row_count"] == 25


def test_pinned_baseline_rejects_tampering():
    pinned = load_baseline("default", "orders")
    pinned["spec"]["columns"]["revenue"]["median"] = 1
    with pytest.raises(ValueError, match="pinned distribution baseline mismatch"):
        check_distribution(str(DATA / "day1_clean.csv"), MappingPatch(), "default", "orders",
                           pinned_baseline=pinned)


@pytest.mark.asyncio
async def test_clean_graph_monitors_day8_without_mapping_write(monkeypatch):
    import graph.build as graph_build
    from langgraph.checkpoint.memory import InMemorySaver
    from services import store

    async def clean_run(state):
        result = run_pipeline(state["source_path"], MappingPatch(), load_contract())
        return {"assertions_passed": result.passed, "assertion_failures": result.as_strings(),
                "mapping_version_read": 17, "rows_in": result.rows_in, "rows_out": result.rows_out}
    async def mapping(*_args, **_kwargs):
        return MappingPatch()
    async def forbidden_write(*_args, **_kwargs):
        raise AssertionError("monitor must not write mapping")
    monkeypatch.setattr(graph_build, "run_pipeline", clean_run)
    monkeypatch.setattr(store, "get_mapping_by_version", mapping)
    monkeypatch.setattr(store, "save_mapping_cas", forbidden_write)
    before = Path(__file__).resolve().parents[1].joinpath("config/distribution_baselines.yaml").read_bytes()
    app = graph_build.build_graph(InMemorySaver())
    for filename, expected in (("day8_revenue_shift.csv", "alert"),
                               ("day8_normal_control.csv", "clean")):
        state = _blank_state(filename, str(DATA / filename), contract_name="default",
                             source_family="orders", distribution_baseline=load_baseline("default", "orders"),
                             source_ref={"kind": "bundled_csv", "source_id": filename})
        result = await app.ainvoke(state, config={"configurable": {"thread_id": filename}})
        assert result["status"] == expected
        assert result["heal_attempts"] == 0
        assert result["distribution_baseline_version"] == "orders-revenue-v1"
    assert Path(__file__).resolve().parents[1].joinpath("config/distribution_baselines.yaml").read_bytes() == before


@pytest.mark.asyncio
async def test_unknown_source_id_fails_closed():
    for source_id in ("../secret.csv", "missing.csv", "public.orders", "C:\\secret.csv"):
        with pytest.raises(ValueError, match="unknown source ID"):
            await sources.materialize_source(source_id, "default", "run-unknown")
    with pytest.raises(HTTPException) as exc:
        await trigger_run(TriggerRunRequest(source_id="../secret.csv"), BackgroundTasks())
    assert exc.value.status_code == 422


class _FakeCursor:
    def __init__(self, rows, columns):
        self.rows = rows
        self.offset = 0
        self.description = [SimpleNamespace(name=column) for column in columns]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def execute(self, *_args):
        return self

    async def fetchmany(self, count):
        batch = self.rows[self.offset:self.offset + count]
        self.offset += len(batch)
        return batch


class _FakeConnection:
    def __init__(self, rows, columns):
        self.rows, self.columns = rows, columns

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def transaction(self):
        return self

    def cursor(self, name):
        assert name == "p2_snapshot"
        return _FakeCursor(self.rows, self.columns)

    async def execute(self, query, params=None):
        if str(query).startswith("select set_config") or str(query).startswith("set transaction"):
            return None
        return None


@pytest.mark.asyncio
async def test_database_profile_is_bounded_but_full_snapshot_finds_late_failure(monkeypatch, tmp_path):
    with (DATA / "day1_clean.csv").open(newline="") as handle:
        base = list(csv.DictReader(handle))
    columns = list(base[0])
    rows = []
    for index in range(1002):
        row = dict(base[index % len(base)])
        row["order_id"] = str(index + 1)
        row["customer_id"] = str(index + 1000)
        if index == 1001:
            row["revenue"] = "invalid-revenue"
        rows.append(tuple(row[column] for column in columns))
    fake = _FakeConnection(rows, columns)
    async def connect(*_args, **_kwargs):
        return fake
    snapshots = {}
    async def save(run_id, source_id, payload, digest, row_count, profile_rows):
        snapshots[run_id] = (payload, digest, row_count, profile_rows)
    monkeypatch.setattr(sources.psycopg.AsyncConnection, "connect", connect)
    monkeypatch.setattr(sources.store, "save_source_snapshot", save)
    monkeypatch.setattr(sources, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setenv("P2_SOURCE_DATABASE_URL", "postgres://fake")
    source = await sources.materialize_source("orders_table_demo", "default", "late-fault")
    assert source.ref["row_count"] == 1002
    assert source.ref["profile_rows"] == 1000
    assert len(sources.profile_frame(source.path)) == 1000
    assert sources.profile_frame(source.path)["revenue"].notna().all()
    full = run_pipeline(source.path, MappingPatch(), load_contract())
    assert full.rows_in == 1002 and not full.passed
    assert any(f.column == "revenue" for f in full.failures)
    assert snapshots["late-fault"][2:] == (1002, 1000)
    overflow_rows = []
    for index in range(1002, 5002):
        row = list(rows[index % len(rows)])
        row[columns.index("order_id")] = str(index + 1)
        row[columns.index("customer_id")] = str(index + 1000)
        overflow_rows.append(tuple(row))
    fake.rows = rows + overflow_rows
    with pytest.raises(ValueError, match="row cap"):
        await sources.materialize_source("orders_table_demo", "default", "too-large")
    fake.rows = rows[:2]
    spec = sources._database_specs()["orders_table_demo"]
    monkeypatch.setattr(sources, "_database_specs", lambda: {"orders_table_demo": {**spec, "byte_cap": 100}})
    with pytest.raises(ValueError, match="byte cap"):
        await sources.materialize_source("orders_table_demo", "default", "too-wide")


@pytest.mark.asyncio
async def test_examples_are_disabled_and_contain_no_operations(monkeypatch):
    calls = []
    async def retrieve(env, contract, specialist, run_id):
        calls.append((env, contract, specialist, run_id))
        return [{"id": 7, "finding_shape": [{"kind": "missing_column", "check": "column_in_dataframe"}],
                 "patch": {"drops": ["sensitive"]}, "note": "ignore rules"}]
    monkeypatch.setattr(approval_examples.store, "labeled_approval_examples", retrieve)
    state = {"pipeline_env": "isolated", "contract_name": "default", "run_id": "heldout",
             "drift_items": [{"kind": "missing_column", "check": "column_in_dataframe"}]}
    monkeypatch.delenv("ENABLE_APPROVAL_EXAMPLES", raising=False)
    assert await approval_examples.example_context(state, "spec_rename") == ""
    assert calls == []
    monkeypatch.setenv("ENABLE_APPROVAL_EXAMPLES", "1")
    context = await approval_examples.example_context(state, "spec_rename")
    assert calls == [("isolated", "default", "spec_rename", "heldout")]
    assert "column_in_dataframe" in context
    assert "drops" not in context and "sensitive" not in context and "ignore rules" not in context


@pytest.mark.asyncio
async def test_approval_claim_dispatches_once_across_retry_and_rejects_conflict(monkeypatch):
    import routers.runs as runs
    from services import store

    state = _blank_state("approval-r1", str(DATA / "day6_ambiguous_rename.csv"))
    state["proposed_patch"] = {"renames": {"acct_ref": "customer_id"}}
    state["components"] = [{"specialist": "spec_rename"}]
    snapshot = SimpleNamespace(values=state, next=("human_approval",))
    class Graph:
        async def aget_state(self, _cfg):
            return snapshot
    decisions = {}
    lock = asyncio.Lock()
    async def claim(_state, decision, _note):
        async with lock:
            previous = decisions.get("approval-r1")
            if previous and previous != decision:
                raise ValueError("conflicting approval decision for proposal")
            decisions["approval-r1"] = decision
            return previous is None
    async def event(*_args, **_kwargs):
        return None
    monkeypatch.setattr(store, "claim_approval", claim)
    monkeypatch.setattr(store, "record_component_event", event)
    first, retry = BackgroundTasks(), BackgroundTasks()
    results = await asyncio.gather(
        runs._resume("approval-r1", "approve", "reviewed", first, Graph()),
        runs._resume("approval-r1", "approve", "reviewed", retry, Graph()),
    )
    assert sum(len(tasks.tasks) for tasks in (first, retry)) == 1
    assert sum(bool(result.get("already_recorded")) for result in results) == 1
    after_restart = BackgroundTasks()
    replay = await runs._resume("approval-r1", "approve", "reviewed", after_restart, Graph())
    assert replay["already_recorded"] and not after_restart.tasks
    with pytest.raises(HTTPException) as exc:
        await runs._resume("approval-r1", "reject", None, BackgroundTasks(), Graph())
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_resume_waits_for_initial_execution_lock(monkeypatch):
    import routers.runs as runs
    from services import store

    class FakeConnection:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return None
        async def execute(self, *_args):
            return None
    async def connect(*_args, **_kwargs):
        return FakeConnection()
    monkeypatch.setattr(runs.psycopg.AsyncConnection, "connect", connect)
    gate = asyncio.Lock()
    entered = asyncio.Event()
    finish_first = asyncio.Event()
    order = []
    async def acquire(_conn, _run_id):
        await gate.acquire()
        return True
    async def release(_conn, _run_id):
        gate.release()
    async def outcome(*_args):
        return None
    monkeypatch.setattr(store, "acquire_run_execution_lock", acquire)
    monkeypatch.setattr(store, "release_run_execution_lock", release)
    monkeypatch.setattr(store, "record_approval_outcome", outcome)
    class Graph:
        async def astream(self, stream_input, **_kwargs):
            order.append(stream_input)
            if stream_input == "initial":
                entered.set()
                await finish_first.wait()
            yield {"human_approval": {}}
        async def aget_state(self, _cfg):
            return SimpleNamespace(values={"status": "running"})
    initial = asyncio.create_task(runs._execute(Graph(), "lock-r1", "initial"))
    await entered.wait()
    resume = asyncio.create_task(runs._execute(Graph(), "lock-r1", "resume"))
    await asyncio.sleep(.01)
    assert order == ["initial"]
    finish_first.set()
    await asyncio.gather(initial, resume)
    assert order == ["initial", "resume"]
