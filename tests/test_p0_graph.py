"""Graph behavior with deterministic LLM responses and an in-memory mapping store."""
import asyncio
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from graph.build import build_graph
from graph.nodes import diagnose, propose_patch, spec_rename, spec_type, spec_value
from pipeline.mapping import MappingPatch
from services import store

DATA = Path(__file__).resolve().parents[1] / "data" / "sources"


class MappingStore:
    def __init__(self):
        self.mapping = MappingPatch()
        self.version = 1
        self.writes = 0
        self.fail_cas = False

    async def get_current_mapping(self):
        return self.mapping

    async def get_current_mapping_version(self):
        return self.mapping, self.version

    async def get_mapping_by_version(self, version_id):
        if version_id != self.version:
            raise ValueError("version not found")
        return self.mapping

    async def save_mapping_cas(self, mapping, expected_id, **kwargs):
        if self.fail_cas:
            raise ValueError("stale mapping")
        if expected_id != self.version:
            raise ValueError("stale mapping")
        self.mapping = mapping
        self.version += 1
        self.writes += 1
        return self.version


def initial(filename):
    return {
        "run_id": filename, "source_path": str(DATA / filename),
        "assertions_passed": False, "assertion_failures": [], "rows_in": 0, "rows_out": 0,
        "actual_schema": {}, "expected_schema": {}, "drift_items": [],
        "drift_class": None, "diagnosis": None, "confidence": None, "candidates": [],
        "proposed_patch": None, "patch_rationale": None, "applied_patch": None,
        "mapping_version_read": None, "mapping_version_written": None,
        "source_sha256": None,
        "patch_error": None, "proposal_error": None, "components": [],
        "awaiting_approval": False, "human_decision": None, "human_note": None,
        "heal_attempts": 0, "status": "running", "final_message": None, "history": [],
        "assertion_failure_details": [], "source_profile": {},
        "specialist_output": None, "specialist_outputs": None,
    }


def graph_fixture(monkeypatch, *, fail_type=False):
    memory = MappingStore()
    monkeypatch.setattr(store, "get_current_mapping", memory.get_current_mapping)
    monkeypatch.setattr(store, "get_current_mapping_version", memory.get_current_mapping_version)
    monkeypatch.setattr(store, "get_mapping_by_version", memory.get_mapping_by_version)
    monkeypatch.setattr(store, "save_mapping_cas", memory.save_mapping_cas)

    async def diagnose_llm(system, prompt):
        import json
        # Read the JSON drift list from the deterministic prompt.
        raw = prompt.split("Drift items (structured diff against the contract):\n", 1)[1].split(
            "\n\nValidation failures", 1)[0]
        items = json.loads(raw)
        kinds = {i["kind"] for i in items}
        if "unexpected_column" in kinds and "missing_column" not in kinds:
            return {"drift_class": "unknown", "diagnosis": "unmatched column", "confidence": 0,
                    "candidates": []}
        if kinds - {"missing_column", "unexpected_column", "dtype_mismatch", "enum_value"}:
            return {"drift_class": "unknown", "diagnosis": "unhandled check", "confidence": 0,
                    "candidates": []}
        candidates = []
        for cls, matched in (("rename", {"missing_column", "unexpected_column"}),
                             ("type", {"dtype_mismatch"}), ("value", {"enum_value"})):
            selected = [i for i in items if i["kind"] in matched]
            if selected:
                candidates.append({"drift_class": cls,
                                   "finding_ids": [i["finding_id"] for i in selected],
                                   "confidence": .85 if any(i["column"] == "acct_ref" for i in selected) else .95})
        return {"drift_class": "mixed" if len(candidates) > 1 else candidates[0]["drift_class"],
                "diagnosis": "deterministic fixture diagnosis", "confidence": min(c["confidence"] for c in candidates),
                "candidates": candidates}

    async def rename_llm(system, prompt):
        return {"renames": {"acct_ref" if "acct_ref" in prompt else "cust_id": "customer_id"}}

    async def type_llm(system, prompt):
        return {"casts": {}} if fail_type else {"casts": {"revenue": "strip_commas_to_float"}}

    async def value_llm(system, prompt):
        return {"value_maps": {"region": {"N": "North", "S": "South", "E": "East", "W": "West"}}}

    async def rationale_llm(system, prompt):
        return {"rationale": "fixture patch"}

    monkeypatch.setattr(diagnose, "call_json", diagnose_llm)
    monkeypatch.setattr(spec_rename, "call_json", rename_llm)
    monkeypatch.setattr(spec_type, "call_json", type_llm)
    monkeypatch.setattr(spec_value, "call_json", value_llm)
    monkeypatch.setattr(propose_patch, "call_json", rationale_llm)
    return build_graph(InMemorySaver()), memory


def run_graph(graph, filename):
    cfg = {"configurable": {"thread_id": filename}, "recursion_limit": 30}
    async def stream():
        async for event in graph.astream(initial(filename), config=cfg, stream_mode="updates"):
            pass
        return (await graph.aget_state(cfg)).values
    return asyncio.run(asyncio.wait_for(stream(), 15),
                       loop_factory=asyncio.ProactorEventLoop), cfg


@pytest.mark.parametrize("filename,expected", [
    ("day7_value_drift.csv", {"value_maps": {"region": {"N": "North", "S": "South", "E": "East", "W": "West"}}}),
    ("day4_combo.csv", {"renames": {"cust_id": "customer_id"}, "casts": {"revenue": "strip_commas_to_float"}}),
    ("day7_mixed.csv", None),
])
def test_one_atomic_attempt(monkeypatch, filename, expected):
    graph, memory = graph_fixture(monkeypatch)
    result, _ = run_graph(graph, filename)
    assert result["status"] == "healed", result.get("final_message")
    assert result["heal_attempts"] == memory.writes == 1
    assert len(result["history"]) == 1
    if filename == "day4_combo.csv":
        assert result["confidence"] == .95
        assert next(c for c in result["components"] if c["drift_class"] == "rename")["confidence"] == .95
    if expected:
        for section, content in expected.items():
            assert result["applied_patch"][section] == content


def test_unknown_and_failed_specialist_do_not_write(monkeypatch):
    graph, memory = graph_fixture(monkeypatch)
    result, _ = run_graph(graph, "day5_unfixable.csv")
    assert result["status"] == "escalated" and result["applied_patch"] is None
    assert memory.writes == 0

    graph, memory = graph_fixture(monkeypatch)
    result, _ = run_graph(graph, "negative_new_category.csv")
    assert result["status"] == "escalated" and memory.writes == 0

    graph, memory = graph_fixture(monkeypatch, fail_type=True)
    result, _ = run_graph(graph, "day4_combo.csv")
    assert result["status"] == "escalated" and result["applied_patch"] is None
    assert memory.writes == 0


def test_low_confidence_interrupt_approve_and_reject(monkeypatch):
    graph, memory = graph_fixture(monkeypatch)
    result, cfg = run_graph(graph, "day6_ambiguous_rename.csv")
    assert result["heal_attempts"] == 0 and memory.writes == 0
    assert result["confidence"] == .4
    assert result["components"][0]["confidence"] == .4
    assert result["components"][0]["safety_status"] == "review: weak lexical rename match"
    assert asyncio.run(graph.aget_state(cfg), loop_factory=asyncio.ProactorEventLoop).next == ("human_approval",)
    resumed = asyncio.run(graph.ainvoke(Command(resume={"decision": "approve"}), config=cfg), loop_factory=asyncio.ProactorEventLoop)
    assert resumed["status"] == "healed" and memory.writes == 1

    graph, memory = graph_fixture(monkeypatch)
    result, cfg = run_graph(graph, "day6_ambiguous_rename.csv")
    resumed = asyncio.run(graph.ainvoke(Command(resume={"decision": "reject"}), config=cfg), loop_factory=asyncio.ProactorEventLoop)
    assert resumed["status"] == "escalated" and memory.writes == 0


def test_cas_failure_does_not_record_an_applied_patch(monkeypatch):
    graph, memory = graph_fixture(monkeypatch)
    memory.fail_cas = True
    result, _ = run_graph(graph, "day7_value_drift.csv")
    assert result["status"] == "escalated"
    assert result["applied_patch"] is None
    assert result["heal_attempts"] == memory.writes == 0
    assert "stale mapping" in result["patch_error"]
