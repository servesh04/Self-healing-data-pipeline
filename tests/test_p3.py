"""P3 boundaries: explicit date parsing, pinned alerts, and named JSON snapshots."""

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from graph.nodes.diagnose import diagnose
from graph.nodes.monitor_temporal import monitor_temporal
from graph.nodes.commit_report import commit_report
from pipeline.contract import load_contract
from pipeline.mapping import MappingPatch
from pipeline.runner import run_pipeline
from pipeline.sources import flatten_record, materialize_source, ensure_source_path
from pipeline.steps import parse_declared_date
from pipeline.temporal import check_temporal, load_temporal_rule

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "data" / "sources"
CLOCK = "2026-10-14T00:00:00+00:00"


@pytest.mark.parametrize("name,checks", [
    ("day9_temporal_control.csv", set()),
    ("day9_future.csv", {"future_date"}),
    ("day9_stale.csv", {"stale_batch", "outside_history_window"}),
])
def test_temporal_alert_only_and_schema_valid(name, checks):
    path = SOURCES / name
    contract = load_contract()
    assert run_pipeline(str(path), MappingPatch(), contract).passed
    pinned = load_temporal_rule("default", "orders_current")
    original = (ROOT / "config" / "temporal_rules.yaml").read_bytes()
    result = check_temporal(str(path), MappingPatch(), "default", "orders_current", CLOCK, pinned)
    assert {alert["check"] for alert in result.alerts} == checks
    assert result.profile["parsed_count"] == 25
    assert result.rule_digest == pinned["digest"]
    assert (ROOT / "config" / "temporal_rules.yaml").read_bytes() == original


def test_date_parse_only_declared_unambiguous_format(tmp_path):
    good = SOURCES / "day9_format_dmy.csv"
    assert not run_pipeline(str(good), MappingPatch(), load_contract()).passed
    patch = MappingPatch(date_parse={"order_date": "dmy_slash_utc"})
    assert run_pipeline(str(good), patch, load_contract()).passed
    # The mapping is a durable full snapshot. A later ISO CSV or an ISO
    # database snapshot in the same scope must still pass unchanged.
    assert run_pipeline(str(SOURCES / "day1_clean.csv"), patch, load_contract()).passed
    mixed = pd.read_csv(good)
    mixed.loc[:9, "order_date"] = "2026-10-13"
    mixed_path = tmp_path / "mixed_explicit_dates.csv"
    mixed.to_csv(mixed_path, index=False)
    assert run_pipeline(str(mixed_path), patch, load_contract()).passed
    control = check_temporal(str(good), patch, "default", "orders_current", CLOCK,
                             load_temporal_rule("default", "orders_current"))
    assert control.alerts == []
    with pytest.raises(ValueError, match="ambiguous"):
        parse_declared_date(pd.Series(["04/05/2026"]), "dmy_slash_utc")
    with pytest.raises(ValueError):
        MappingPatch(date_parse={"order_date": "guessed_format"})
    with pytest.raises(ValueError, match="ambiguous"):
        parse_declared_date(pd.read_csv(SOURCES / "day9_ambiguous.csv")["order_date"], "dmy_slash_utc")
    with pytest.raises(ValueError, match="malformed"):
        parse_declared_date(pd.Series(["2026/10/13", "2026-10-13"]), "dmy_slash_utc")


@pytest.mark.asyncio
async def test_date_diagnosis_fails_closed_without_llm(monkeypatch):
    async def no_llm(*args, **kwargs):
        raise AssertionError("date diagnosis must not ask the generic LLM")
    monkeypatch.setattr("graph.nodes.diagnose.call_json", no_llm)
    finding = {"column": "order_date", "kind": "null_violation", "finding_id": "order_date:not_nullable"}
    state = {"drift_items": [finding], "contract_name": "default",
             "source_path": str(SOURCES / "day9_format_dmy.csv")}
    assert (await diagnose(state))["drift_class"] == "date_parse"
    state["source_path"] = str(SOURCES / "day9_ambiguous.csv")
    assert (await diagnose(state))["drift_class"] == "unknown"


def test_json_flatten_rejects_collision_arrays_and_depth():
    assert flatten_record({"order": {"id": 1}, "region": None}, 2, "_") == {"order_id": 1, "region": None}
    with pytest.raises(ValueError, match="separator"):
        flatten_record({"order": {"id": 1}, "order_id": 2}, 2, "_")
    with pytest.raises(ValueError, match="array"):
        flatten_record({"tags": ["x"]}, 2)
    with pytest.raises(ValueError, match="depth"):
        flatten_record({"a": {"b": {"c": 1}}}, 2)


@pytest.mark.asyncio
async def test_named_json_source_persists_normalized_snapshot(monkeypatch, tmp_path):
    import pipeline.sources as sources
    save = AsyncMock()
    load = AsyncMock()
    monkeypatch.setattr(sources.store, "save_source_snapshot", save)
    monkeypatch.setattr(sources.store, "load_source_snapshot", load)
    monkeypatch.setattr(sources, "SNAPSHOT_DIR", tmp_path)
    materialized = await materialize_source("orders_json_demo", "default", "p3-test-json")
    assert materialized.ref["kind"] == "local_json"
    assert run_pipeline(materialized.path, MappingPatch(), load_contract()).passed
    payload = Path(materialized.path).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == materialized.sha256
    load.return_value = payload
    Path(materialized.path).unlink()
    assert await ensure_source_path({"source_path": materialized.path, "source_ref": materialized.ref,
                                     "run_id": "p3-test-json"}) == materialized.path
    assert Path(materialized.path).read_bytes() == payload
    save.assert_awaited_once()
    with pytest.raises(ValueError, match="unknown source ID"):
        await materialize_source("../day10_nested_orders.json", "default", "bad")


@pytest.mark.asyncio
async def test_json_encoded_byte_cap_includes_multibyte_separator(monkeypatch, tmp_path):
    import json
    import pipeline.sources as sources
    source = tmp_path / "nested.json"
    source.write_text(json.dumps({"records": [{"a": {"b": "é"}}]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sources, "CSV_DIR", tmp_path)
    spec = {"file": source.name, "contract_name": "default", "records_path": "records",
            "row_cap": 10, "byte_cap": 100, "max_depth": 2, "separator": "é" * 60}
    with pytest.raises(ValueError, match="normalized JSON exceeds byte cap"):
        await sources._json_source("fixture", spec, "default", "no-write")


@pytest.mark.asyncio
async def test_temporal_graph_node_alerts_without_mapping_write(monkeypatch):
    from services import store
    async def mapping(*args, **kwargs):
        return MappingPatch()
    async def no_write(*args, **kwargs):
        raise AssertionError("monitor must not write mapping")
    monkeypatch.setattr(store, "get_mapping_by_version", mapping)
    monkeypatch.setattr(store, "save_mapping_cas", no_write)
    state = {"source_path": str(SOURCES / "day9_future.csv"),
             "source_ref": {"kind": "bundled_csv", "source_id": "day9_future.csv"},
             "contract_name": "default", "source_family": "orders_current",
             "contract_snapshot": load_contract().spec, "mapping_version_read": 1,
             "run_clock_utc": CLOCK, "temporal_rule": load_temporal_rule("default", "orders_current"),
             "heal_attempts": 0}
    update = await monitor_temporal(state)
    assert update["temporal_alerts"][0]["check"] == "future_date"
    assert commit_report({**state, **update})["status"] == "alert"
    skipped = await monitor_temporal({**state, "temporal_rule": None, "run_clock_utc": None})
    assert skipped["temporal_skipped"][0]["reason"] == "no temporal rule"


@pytest.mark.asyncio
async def test_analytics_separates_temporal_and_distribution_alerts(monkeypatch):
    from routers.analytics import summary
    async def runs(*args, **kwargs):
        return [
            {"status": "alert", "heal_attempts": 0, "approved_by": None,
             "distribution_alerts": [], "temporal_alerts": [{"check": "future_date"}]},
            {"status": "alert", "heal_attempts": 0, "approved_by": None,
             "distribution_alerts": [{"check": "median_shift_iqr"}], "temporal_alerts": []},
        ]
    monkeypatch.setattr("routers.analytics.collect_run_summaries", runs)
    result = await summary(graph_app=None)
    assert (result["alert_runs"], result["distribution_alerts"], result["temporal_alerts"]) == (2, 1, 1)
