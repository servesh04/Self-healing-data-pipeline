"""P0 safety checks against real Pandera failures and the local CSV fixtures."""
import asyncio
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from graph.nodes.diff_contract import diff_contract
from graph.nodes.multi_specialist import merge_fragments
from graph.nodes.apply_patch import merge_mapping
from graph.nodes.propose_patch import propose_patch
from graph.nodes.propose_patch import _obvious_rename
from graph.nodes.spec_value import _mechanical_alias
from graph.nodes import diagnose as diagnose_module
from prompts.prompts import SPEC_VALUE_SYSTEM
from graph.nodes.profile_source import profile_source
from graph.routing import route_after_confidence, route_after_diagnose
from pipeline.contract import load_contract
from pipeline.mapping import MappingPatch
from pipeline.runner import run_pipeline
from pipeline.steps import apply_mapping

DATA = Path(__file__).resolve().parents[1] / "data" / "sources"


def findings(filename):
    result = run_pipeline(str(DATA / filename), MappingPatch(), load_contract())
    details = [vars(f) for f in result.failures]
    return diff_contract({"assertion_failure_details": details})["drift_items"]


def test_enum_evidence_and_bounded_profile():
    items = findings("day7_value_drift.csv")
    assert len(items) == 1
    assert items[0]["kind"] == "enum_value"
    assert "North" in items[0]["expected"]
    assert items[0]["failure_count"] == 50
    profile = profile_source({"source_path": str(DATA / "day7_value_drift.csv")})["source_profile"]
    assert profile["region"]["frequencies"] == {"N": 13, "S": 13, "E": 12, "W": 12}
    assert profile["region"]["truncated"] is False
    assert _mechanical_alias("N", "North", load_contract().allowed_values["region"])
    assert not _mechanical_alias("Central", "North", load_contract().allowed_values["region"])


def test_high_cardinality_profile_is_bounded(tmp_path):
    path = tmp_path / "high_cardinality.csv"
    pd.DataFrame({"region": [f"category-{i}" for i in range(40)]}).to_csv(path, index=False)
    region = profile_source({"source_path": str(path)})["source_profile"]["region"]
    assert region["distinct_count"] == 40
    assert region["truncated"] is True
    assert len(region["frequencies"]) == 32


def test_value_specialist_prompt_requests_json_mode():
    assert "json" in SPEC_VALUE_SYSTEM.casefold()


def test_incomplete_mixed_diagnosis_retries_exact_coverage(monkeypatch):
    items = findings("day7_mixed.csv")
    by_kind = {kind: [i["finding_id"] for i in items if i["kind"] == kind]
               for kind in ("missing_column", "unexpected_column", "dtype_mismatch", "enum_value")}
    rename = {"drift_class": "rename", "finding_ids": by_kind["missing_column"] + by_kind["unexpected_column"],
              "confidence": .92}
    cast = {"drift_class": "type", "finding_ids": by_kind["dtype_mismatch"], "confidence": .91}
    value = {"drift_class": "value", "finding_ids": by_kind["enum_value"], "confidence": .9}
    responses = [
        {"drift_class": "mixed", "diagnosis": "incomplete", "confidence": .91,
         "candidates": [rename, cast]},
        {"drift_class": "mixed", "diagnosis": "complete", "confidence": .93,
         "candidates": [rename, cast, value]},
    ]
    calls = []

    async def llm(system, prompt):
        calls.append(prompt)
        return responses.pop(0)

    monkeypatch.setattr(diagnose_module, "call_json", llm)
    result = asyncio.run(diagnose_module.diagnose({"drift_items": items,
                                                   "assertion_failures": [], "source_profile": {}}))
    assert len(calls) == 2
    assert "Missing finding IDs" in calls[1]
    assert result["drift_class"] == "mixed"
    assert {fid for c in result["candidates"] for fid in c["finding_ids"]} == {i["finding_id"] for i in items}
    assert result["confidence"] == .9


def test_unknown_diagnosis_never_retries_or_dispatches(monkeypatch):
    calls = []

    async def llm(system, prompt):
        calls.append(prompt)
        return {"drift_class": "unknown", "diagnosis": "unhandled column", "confidence": 0,
                "candidates": []}

    monkeypatch.setattr(diagnose_module, "call_json", llm)
    result = asyncio.run(diagnose_module.diagnose({"drift_items": findings("day5_unfixable.csv"),
                                                   "assertion_failures": [], "source_profile": {}}))
    assert len(calls) == 1
    assert route_after_diagnose(result) == "escalate"


@pytest.mark.parametrize("filename,kind", [
    ("negative_minimum.csv", "minimum"),
    ("negative_unique.csv", "unique"),
])
def test_other_checks_are_not_enum(filename, kind):
    assert {item["kind"] for item in findings(filename)} == {kind}


def test_value_map_validates_targets_and_preserves_nulls_and_existing_labels():
    patch = MappingPatch(value_maps={"region": {"N": "North"}})
    result = apply_mapping(pd.DataFrame({"region": ["N", "South", None, "X"]}), patch)
    assert result["region"].tolist()[:2] == ["North", "South"]
    assert pd.isna(result["region"].iloc[2])
    assert result["region"].iloc[3] == "X"
    with pytest.raises(ValidationError):
        MappingPatch(value_maps={"region": {"N": "Central"}})
    with pytest.raises(ValidationError):
        MappingPatch(value_maps={"region": {"North": "South"}})
    with pytest.raises(ValidationError):
        MappingPatch(value_maps={"revenue": {"1": "2"}})


def test_mixed_patch_normalizes_post_rename_and_passes():
    patch = merge_fragments([
        {"renames": {"cust_id": "customer_id"}},
        {"casts": {"revenue": "strip_commas_to_float"}},
        {"value_maps": {"region": {"N": "North", "S": "South", "E": "East", "W": "West"}}},
    ], {"cust_id": "customer_id"})
    assert run_pipeline(str(DATA / "day7_mixed.csv"), MappingPatch.model_validate(patch), load_contract()).passed
    normalized = merge_fragments([{"renames": {"cust_id": "customer_id"}},
                                  {"null_policy": {"cust_id": "fill_zero"}}],
                                 {"cust_id": "customer_id"})
    assert normalized["null_policy"] == {"customer_id": "fill_zero"}


def test_conflicting_writes_and_unknown_routing():
    with pytest.raises(ValueError):
        merge_fragments([{"casts": {"revenue": "strip_commas_to_float"}},
                         {"casts": {"revenue": "strip_commas_to_float"}}])
    with pytest.raises(ValueError):
        merge_fragments([{"value_maps": {"region": {"N": "North"}}},
                         {"value_maps": {"region": {"N": "South"}}}])
    assert route_after_diagnose({"drift_class": "unknown", "candidates": [
        {"drift_class": "value", "finding_ids": ["x"], "confidence": 1.0}]}) == "escalate"
    assert route_after_confidence({"confidence": 1.0, "proposed_patch": {}}) == "escalate"
    with pytest.raises(ValueError):
        merge_mapping(MappingPatch(renames={"old": "customer_id"}),
                      {"renames": {"old": "customer_id"}})
    with pytest.raises(ValueError):
        merge_mapping(MappingPatch(value_maps={"region": {"N": "North"}}),
                      {"value_maps": {"region": {"N": "South"}}})


def test_stale_specialist_outputs_cannot_override_a_new_single_proposal(monkeypatch):
    from graph.nodes import propose_patch as module

    async def rationale(*args):
        return {"rationale": "ok"}

    monkeypatch.setattr(module, "call_json", rationale)
    state = {"drift_class": "rename", "diagnosis": "new cycle",
             "candidates": [{"drift_class": "rename", "confidence": .9, "finding_ids": ["a", "b"]}],
             "specialist_output": {"renames": {"cust_id": "customer_id"}},
             "specialist_outputs": [{"casts": {"revenue": "strip_commas_to_float"}}],
             "components": [], "proposal_error": None}
    result = asyncio.run(propose_patch(state))
    assert result["proposed_patch"] == {"renames": {"cust_id": "customer_id"}}


@pytest.mark.parametrize("source,expected_confidence,expected_route", [
    ("cust_id", .85, "apply_patch"),
    ("acct_ref", .4, "human_approval"),
])
def test_rename_confidence_is_capped_only_for_weak_lexical_match(
    monkeypatch, source, expected_confidence, expected_route
):
    from graph.nodes import propose_patch as module

    async def rationale(*args):
        return {"rationale": "rename"}

    monkeypatch.setattr(module, "call_json", rationale)
    state = {"drift_class": "rename", "diagnosis": "LLM score high", "confidence": .85,
             "candidates": [{"drift_class": "rename", "confidence": .85,
                              "finding_ids": ["missing", "unexpected"]}],
             "specialist_output": {"renames": {source: "customer_id"}},
             "components": [], "proposal_error": None}
    result = asyncio.run(propose_patch(state))
    assert result["confidence"] == expected_confidence
    assert result["components"][0]["confidence"] == expected_confidence
    assert route_after_confidence(result) == expected_route
    assert _obvious_rename(source, "customer_id") is (source == "cust_id")
