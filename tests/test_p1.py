"""P1 contract, mapping-version and calibration invariants without a live DB."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from pipeline.contract import ContractRegistry, contract_for_state, get_registry
from pipeline.mapping import MappingPatch
from graph.nodes.rerun_validate import _worsened
from graph.nodes.rerun_validate import rerun_validate
from graph.nodes.run_pipeline import run_pipeline as run_node
from pipeline.contract import FailureCase
from services.calibration import aggregate_component_events
from services import store
from routers.runs import _resolve_source_path
from routers.runs import get_run
from routers.runs import TriggerRunRequest, trigger_run, _execute
from fastapi import BackgroundTasks
from services import analytics as analytics_service
from fastapi import HTTPException


def test_registry_second_contract_and_pinned_snapshot(tmp_path):
    default = get_registry().get("default")
    compact = get_registry().get("compact")
    assert set(default.expected_dtypes) != set(compact.expected_dtypes)
    assert default.version != compact.version
    pinned = {"contract_name": "compact", "contract_version": compact.version,
              "contract_snapshot": compact.spec}
    assert contract_for_state(pinned).version == compact.version
    with pytest.raises(ValueError, match="hash mismatch"):
        contract_for_state({**pinned, "contract_version": default.version})
    with pytest.raises(ValueError, match="unknown contract"):
        get_registry().get("missing")
    (tmp_path / "default.yaml").write_text("columns: {id: {dtype: int64}}\n", encoding="utf-8")
    (tmp_path / "default.yaml").write_text("columns:\n  id: {dtype: int64}\n  id: {dtype: str}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate contract key"):
        ContractRegistry(tmp_path)


def test_mapping_model_is_revalidated_for_target_contract():
    wrong = MappingPatch(value_maps={"region": {"N": "North"}})
    with pytest.raises(ValueError, match="allowed-value column"):
        store._validated(wrong, "compact")


@pytest.mark.asyncio
async def test_graph_uses_pinned_compact_contract_when_registry_changes(monkeypatch):
    compact = get_registry().get("compact")
    async def mapping_for_scope(env, name, contract):
        assert (env, name, contract.version) == ("test", "compact", compact.version)
        return MappingPatch(), 12
    monkeypatch.setattr(store, "get_current_mapping_version", mapping_for_scope)
    monkeypatch.setattr("pipeline.contract.get_registry", lambda: (_ for _ in ()).throw(ValueError("registry changed")))
    result = await run_node({"source_path": str(Path(__file__).resolve().parents[1] / "data" / "sources" / "compact_clean.csv"),
                             "contract_name": "compact", "contract_version": compact.version,
                             "contract_snapshot": compact.spec, "pipeline_env": "test"})
    assert result["assertions_passed"] is True
    assert result["mapping_version_read"] == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("current_count,expect_rollback", [(2, False), (5, True)])
async def test_rerun_partial_improvement_or_worsening(monkeypatch, current_count, expect_rollback):
    import hashlib
    import graph.nodes.rerun_validate as rerun_module
    source = Path(__file__).resolve().parents[1] / "data" / "sources" / "compact_clean.csv"
    compact = get_registry().get("compact")
    class Result:
        passed = False
        rows_out = 3
        failures = [FailureCase("segment", "isin", current_count, ["Other"])]
        def as_strings(self):
            return ["segment: isin"]
    async def mapping(*args, **kwargs):
        return MappingPatch()
    async def rollback(*args, **kwargs):
        return 13
    async def event(*args, **kwargs):
        return None
    monkeypatch.setattr(store, "get_mapping_by_version", mapping)
    monkeypatch.setattr(store, "rollback_mapping_cas", rollback)
    monkeypatch.setattr(store, "record_component_event", event)
    monkeypatch.setattr(rerun_module, "run_the_pipeline", lambda *args: Result())
    state = {"source_path": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
             "contract_name": "compact", "contract_version": compact.version,
             "contract_snapshot": compact.spec, "pipeline_env": "test", "run_id": "p1-run",
             "mapping_version_read": 11, "mapping_version_written": 12,
             "baseline_failures": [{"column": "segment", "check": "isin", "failure_count": 4}],
             "heal_attempts": 1, "components": [], "confidence": .9}
    output = await rerun_validate(state)
    assert output["rollback_version"] == (13 if expect_rollback else None)
    assert output["mapping_version_read"] == (11 if expect_rollback else 12)
    assert bool(output["patch_error"]) is expect_rollback


@pytest.mark.asyncio
async def test_rollback_conflict_preserves_actual_failure_and_escalates(monkeypatch):
    import hashlib
    import graph.nodes.rerun_validate as rerun_module
    source = Path(__file__).resolve().parents[1] / "data" / "sources" / "compact_clean.csv"
    compact = get_registry().get("compact")
    calls = []
    class Result:
        passed = False
        rows_out = 3
        failures = [FailureCase("segment", "isin", 5, ["Other"])]
        def as_strings(self):
            return ["segment: isin (5 rows)"]
    async def mapping(*args, **kwargs):
        return MappingPatch()
    async def rollback(*args, **kwargs):
        calls.append(1)
        raise ValueError("mapping changed after failed patch")
    async def event(*args, **kwargs):
        return None
    monkeypatch.setattr(store, "get_mapping_by_version", mapping)
    monkeypatch.setattr(store, "rollback_mapping_cas", rollback)
    monkeypatch.setattr(store, "record_component_event", event)
    monkeypatch.setattr(rerun_module, "run_the_pipeline", lambda *args: Result())
    state = {"source_path": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
             "contract_name": "compact", "contract_version": compact.version,
             "contract_snapshot": compact.spec, "pipeline_env": "test", "run_id": "p1-run",
             "mapping_version_read": 11, "mapping_version_written": 12,
             "baseline_failures": [{"column": "segment", "check": "isin", "failure_count": 4}],
             "heal_attempts": 1, "components": [], "confidence": .9}
    output = await rerun_validate(state)
    assert len(calls) == 1
    assert output["assertion_failures"] == ["segment: isin (5 rows)"]
    assert output["mapping_version_read"] == 11
    assert "requires reconciliation" in output["patch_error"]


def test_stable_worsening_rule():
    baseline = [{"column": "region", "check": "isin", "failure_count": 4}]
    assert not _worsened(baseline, [FailureCase("region", "isin", 2, [])])
    assert _worsened(baseline, [FailureCase("region", "isin", 5, [])])
    assert _worsened(baseline, [FailureCase("revenue", "dtype", 1, [])])


def test_source_ids_are_allowlisted_bundled_csvs():
    assert Path(_resolve_source_path("compact_clean.csv")).name == "compact_clean.csv"
    for candidate in ("../compact_clean.csv", "C:\\tmp\\file.csv", "missing.csv", "README.md"):
        with pytest.raises(HTTPException) as exc:
            _resolve_source_path(candidate)
        assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_run_detail_audit_uses_stored_scope(monkeypatch):
    from types import SimpleNamespace
    class Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values={"status": "clean", "contract_name": "default"},
                                   tasks=(), next=())
    async def record(_run_id):
        return {"pipeline_env": "isolated", "contract_name": "compact"}
    seen = []
    async def history(run_id, env, contract_name):
        seen.append((run_id, env, contract_name))
        return []
    monkeypatch.setattr(store, "get_run_record", record)
    monkeypatch.setattr(store, "mapping_history_for_run", history)
    async def approvals(_run_id):
        return []
    monkeypatch.setattr(store, "approval_history", approvals)
    result = await get_run("r1", graph_app=Graph())
    assert result["run_id"] == "r1"
    assert seen == [("r1", "isolated", "compact")]


@pytest.mark.asyncio
async def test_run_creation_and_completion_invalidate_only_selected_scope(monkeypatch):
    from types import SimpleNamespace
    import routers.runs as runs_module
    env = "p1-cache-route"
    default_key = (env, "default")
    compact_key = (env, "compact")
    analytics_service._cache[default_key] = (0, [{"stale": True}])
    analytics_service._cache[compact_key] = (0, [{"keep": True}])
    async def record(*args, **kwargs):
        return None
    monkeypatch.setattr(store, "record_run", record)
    monkeypatch.setattr(runs_module, "get_settings", lambda: SimpleNamespace(pipeline_env=env))
    background = BackgroundTasks()
    created = await trigger_run(TriggerRunRequest(source_path="day1_clean.csv"), background, graph_app=None)
    assert created["run_id"]
    assert default_key not in analytics_service._cache
    assert compact_key in analytics_service._cache
    class Graph:
        async def astream(self, *_args, **_kwargs):
            if False:
                yield None
    analytics_service._cache[default_key] = (0, [{"stale": True}])
    await _execute(Graph(), created["run_id"], {}, env, "default")
    assert default_key not in analytics_service._cache
    assert compact_key in analytics_service._cache
    analytics_service._cache.clear()
    analytics_service._generation.clear()


def test_calibration_never_equates_pass_or_approval_to_correctness():
    rows = [
        {"event_type": "proposal", "labeled_correct": True},
        {"event_type": "proposal", "labeled_correct": False},
        {"event_type": "proposal", "labeled_correct": None},
        {"event_type": "decision", "decision": "human_approved"},
        {"event_type": "decision", "decision": "human_rejected"},
        {"event_type": "decision", "decision": "auto_applied"},
        {"event_type": "outcome", "validation_passed": True},
        {"event_type": "outcome", "validation_passed": False},
        {"event_type": "outcome", "validation_passed": None},
    ]
    metrics = aggregate_component_events(rows)
    assert metrics["proposals"] == 3
    assert metrics["human_approval_rate"] == .5
    assert metrics["human_approval_denominator"] == 2
    assert metrics["validation_denominator"] == 2
    assert metrics["rejected_without_validation"] == 1
    assert metrics["labeled_correctness_rate"] == .5
    assert metrics["labeled_denominator"] == 2
    assert metrics["labeled_correctness_ci95"] is not None
    assert metrics["adaptive_threshold_enabled"] is False
    assert aggregate_component_events(rows[2:])["labeled_denominator"] == 0


class _Cursor:
    def __init__(self, row=None):
        self.row = row

    async def fetchone(self):
        return self.row


class _MemoryPool:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.rows = {1: {"mapping": MappingPatch().model_dump(), "parent_id": None,
                         "pipeline_env": "test", "contract_name": "default"},
                     2: {"mapping": MappingPatch().model_dump(), "parent_id": None,
                         "pipeline_env": "test", "contract_name": "compact"}}
        self.current = {("test", "default"): 1, ("test", "compact"): 2}
        self.next_id = 3

    @asynccontextmanager
    async def connection(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            yield self

    async def execute(self, sql, params=()):
        normalized = " ".join(sql.lower().split())
        if "pg_advisory_xact_lock" in normalized:
            return _Cursor()
        if "select s.id, s.mapping from mapping_current" in normalized:
            version = self.current.get(tuple(params))
            return _Cursor({"id": version, "mapping": self.rows[version]["mapping"]} if version else None)
        if "select current_version_id" in normalized:
            version = self.current.get(tuple(params))
            return _Cursor({"id": version, "current_version_id": version} if version else None)
        if "select parent_id from mapping_state" in normalized:
            version, env, name = params
            row = self.rows.get(version)
            return _Cursor({"parent_id": row["parent_id"]} if row and (row["pipeline_env"], row["contract_name"]) == (env, name) else None)
        if "select mapping from mapping_state" in normalized:
            if "id = %s" in normalized and len(params) == 3:
                if isinstance(params[0], int):
                    version, env, name = params
                else:
                    env, name, version = params
                row = self.rows.get(version)
                return _Cursor({"mapping": row["mapping"]} if row and (row["pipeline_env"], row["contract_name"]) == (env, name) else None)
        if "insert into mapping_state" in normalized:
            version = self.next_id
            self.next_id += 1
            if "'rollback'" in normalized:
                mapping, env, name, _run, parent, _restores, _reason = params
            else:
                mapping, _who, env, name, _run, parent = params
            self.rows[version] = {"mapping": mapping.obj, "parent_id": parent,
                                  "pipeline_env": env, "contract_name": name}
            return _Cursor({"id": version})
        if "update mapping_current" in normalized:
            version, env, name = params
            self.current[(env, name)] = version
            return _Cursor()
        raise AssertionError(normalized)


@pytest.mark.asyncio
async def test_scoped_cas_concurrent_stale_writer_and_exact_rollback(monkeypatch):
    memory = _MemoryPool()
    monkeypatch.setattr(store, "get_pool", lambda: memory)
    default, default_id = await store.get_current_mapping_version("test", "default")
    compact, compact_id = await store.get_current_mapping_version("test", "compact")
    assert default_id == 1 and compact_id == 2

    async def write(source):
        return await store.save_mapping_cas(MappingPatch(renames={source: "customer_id"}),
                                            1, "test", pipeline_env="test")

    outcomes = await asyncio.gather(write("cust_id"), write("acct_ref"), return_exceptions=True)
    assert sum(isinstance(value, int) for value in outcomes) == 1
    assert sum(isinstance(value, ValueError) for value in outcomes) == 1
    written = next(value for value in outcomes if isinstance(value, int))
    assert memory.current[("test", "compact")] == compact_id
    assert (await store.get_current_mapping_version("test", "compact"))[0] == compact
    restored = await store.rollback_mapping_cas(written, "run", "worsened", "test")
    assert memory.rows[restored]["mapping"] == default.model_dump()
    assert memory.rows[restored]["parent_id"] == written
    assert memory.rows[written]["parent_id"] == 1
    with pytest.raises(ValueError, match="rollback conflict"):
        await store.rollback_mapping_cas(written, "run", "again", "test")


@pytest.mark.asyncio
async def test_legacy_migration_is_idempotent_and_keeps_default_scope(monkeypatch):
    sql = []
    class Capture:
        @asynccontextmanager
        async def connection(self):
            yield self
        async def execute(self, statement, params=()):
            sql.append(" ".join(statement.lower().split()))
            return _Cursor()
    monkeypatch.setattr(store, "get_pool", lambda: Capture())
    await store.init_mapping_table()
    await store.init_runs_table()
    assert any("add column if not exists contract_name text not null default 'default'" in s for s in sql)
    assert any("insert into mapping_current" in s and "on conflict" in s for s in sql)
    assert any("where mapping_scope is null" in s for s in sql)
