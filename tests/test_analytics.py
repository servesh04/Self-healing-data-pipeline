"""Analytics aggregation, in two layers:

- services/analytics.py: collect_run_summaries' own field-derivation logic
  (which fields come from `history` vs top-level state), caching, and the
  request-coalescing that exists specifically to stop N concurrent
  dashboard callers from each re-walking every run's checkpoint.
- routers/analytics.py: the plain arithmetic each /api/analytics/*
  endpoint does over collect_run_summaries' output. Cases pinning a
  specific historical bug in this math live in test_regressions.py; the
  cases here are the surrounding contract (normal cases, zero-run edge
  cases) that file doesn't cover.

Both layers are tested by monkeypatching the one seam each needs
(services.analytics.store / services.analytics._completed_at for the
first; routers.analytics.collect_run_summaries for the second) rather than
touching a real checkpointer or Postgres.
"""

import asyncio
from datetime import datetime

import pytest
import services.analytics as analytics_module
from routers.analytics import confidence, specialist_performance, summary

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_analytics_cache():
    """collect_run_summaries' cache and in-flight-request registry are
    module-level global state, keyed by pipeline_env -- left dirty, one
    test's cached result could leak into the next. Cleared before AND
    after every test in this file, on top of each test using its own
    unique pipeline_env string as a belt-and-suspenders isolation key.
    """
    analytics_module._cache.clear()
    analytics_module._pending.clear()
    yield
    analytics_module._cache.clear()
    analytics_module._pending.clear()


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn


class TestCollectRunSummariesFieldDerivation:
    async def test_final_confidence_and_drift_class_come_from_the_last_history_entry(
        self, monkeypatch, make_graph_app, make_snapshot
    ):
        """HealState's own top-level `confidence`/`drift_class` reflect
        only the MOST RECENT diagnose call, which goes stale the moment a
        later diagnose call overwrites it (the same Phase 5 staleness bug
        deriveVisited/PatchDiff had) -- so the per-attempt `history` list
        is the only reliable source for "what actually healed this run".
        """
        values = {
            "status": "healed",
            "drift_class": "type",  # stale: this is what the SECOND diagnose call overwrote it to
            "confidence": 0.5,  # also stale
            "heal_attempts": 2,
            "history": [
                {"specialist": "spec_rename", "result": "failed", "confidence": 0.92, "approved_by": "auto"},
                {"specialist": "spec_type", "result": "passed", "confidence": 0.9, "approved_by": "auto"},
            ],
            "human_decision": None,
        }
        graph_app = make_graph_app({"r1": make_snapshot(values)})
        monkeypatch.setattr(
            analytics_module.store,
            "list_run_records",
            _async_return([{"run_id": "r1", "source_path": "data/sources/day4_combo.csv", "created_at": None}]),
        )
        monkeypatch.setattr(analytics_module, "_completed_at", _async_return(None))

        results = await analytics_module.collect_run_summaries(graph_app, "field-derivation")

        assert len(results) == 1
        assert results[0]["final_confidence"] == 0.9  # the LAST attempt's, not the stale top-level 0.5
        assert results[0]["drift_class"] == "type"  # the last attempt's specialist, not the stale top-level field

    async def test_falls_back_to_top_level_fields_when_history_is_empty(
        self, monkeypatch, make_graph_app, make_snapshot
    ):
        """An unknown-classified escalate never reaches a specialist, so
        `history` is genuinely empty -- there is nothing stale to prefer
        over diagnose's own last write, which IS accurate here (see
        services/analytics.py's module docstring).
        """
        values = {
            "status": "escalated",
            "drift_class": "unknown",
            "confidence": 0.0,
            "heal_attempts": 0,
            "history": [],
            "human_decision": None,
        }
        graph_app = make_graph_app({"r1": make_snapshot(values)})
        monkeypatch.setattr(
            analytics_module.store,
            "list_run_records",
            _async_return([{"run_id": "r1", "source_path": "data/sources/day5_unfixable.csv", "created_at": None}]),
        )
        monkeypatch.setattr(analytics_module, "_completed_at", _async_return(None))

        results = await analytics_module.collect_run_summaries(graph_app, "fallback-fields")

        assert results[0]["final_confidence"] == 0.0
        assert results[0]["drift_class"] == "unknown"

    async def test_status_reflects_a_live_interrupt_over_the_stale_state_field(
        self, monkeypatch, make_graph_app, make_snapshot
    ):
        """Same rule _serialize_state enforces for the single-run endpoint
        (test_serialization.py) applies here for the aggregate view: a
        genuinely paused run must report "awaiting_approval" even though
        HealState's own `status` field still says "running" underneath.
        """
        values = {"status": "running", "heal_attempts": 0, "history": [], "human_decision": None}
        graph_app = make_graph_app(
            {
                "r1": make_snapshot(
                    values, interrupted=True, interrupt_value={"confidence": 0.4}, next=("human_approval",)
                )
            }
        )
        monkeypatch.setattr(
            analytics_module.store,
            "list_run_records",
            _async_return([{"run_id": "r1", "source_path": "data/sources/day6_ambiguous_rename.csv", "created_at": None}]),
        )
        monkeypatch.setattr(analytics_module, "_completed_at", _async_return(None))

        results = await analytics_module.collect_run_summaries(graph_app, "live-interrupt")

        assert results[0]["status"] == "awaiting_approval"

    async def test_duration_ms_is_the_gap_between_created_at_and_the_last_checkpoint(
        self, monkeypatch, make_graph_app, make_snapshot
    ):
        graph_app = make_graph_app({"r1": make_snapshot({"status": "clean", "heal_attempts": 0, "history": []})})
        created_at = datetime(2024, 1, 1, 12, 0, 0)
        completed_at = datetime(2024, 1, 1, 12, 0, 5)  # 5 seconds later
        monkeypatch.setattr(
            analytics_module.store,
            "list_run_records",
            _async_return([{"run_id": "r1", "source_path": "data/sources/day1_clean.csv", "created_at": created_at}]),
        )
        monkeypatch.setattr(analytics_module, "_completed_at", _async_return(completed_at))

        results = await analytics_module.collect_run_summaries(graph_app, "duration")

        assert results[0]["duration_ms"] == 5000


class TestCollectRunSummariesCachingAndCoalescing:
    async def test_a_second_call_within_the_ttl_does_not_recompute(self, monkeypatch, make_graph_app):
        call_count = {"n": 0}

        async def counting_list_run_records(limit=500, pipeline_env=None):
            call_count["n"] += 1
            return []

        monkeypatch.setattr(analytics_module.store, "list_run_records", counting_list_run_records)
        graph_app = make_graph_app({})

        first = await analytics_module.collect_run_summaries(graph_app, "cache-hit")
        second = await analytics_module.collect_run_summaries(graph_app, "cache-hit")

        assert first == second == []
        assert call_count["n"] == 1

    async def test_concurrent_callers_for_the_same_pipeline_env_coalesce_into_one_computation(
        self, monkeypatch, make_graph_app
    ):
        """The exact fix for the real bug found live: the Overview page's
        4-way Promise.all was quadrupling contention for the same
        8-connection pool because a plain result cache doesn't help until
        SOMETHING has already finished once. asyncio.Event forces several
        callers to genuinely overlap here (all mid-flight before the
        underlying computation is allowed to finish), the condition the
        original bug needed to reproduce.
        """
        call_count = {"n": 0}
        release = asyncio.Event()

        async def gated_list_run_records(limit=500, pipeline_env=None):
            call_count["n"] += 1
            await release.wait()
            return []

        monkeypatch.setattr(analytics_module.store, "list_run_records", gated_list_run_records)
        graph_app = make_graph_app({})

        callers = [
            asyncio.ensure_future(analytics_module.collect_run_summaries(graph_app, "coalesce"))
            for _ in range(5)
        ]
        await asyncio.sleep(0)  # let every caller register itself in _pending before releasing
        release.set()
        results = await asyncio.gather(*callers)

        assert call_count["n"] == 1
        assert results == [[]] * 5

    async def test_warm_cache_never_raises_even_when_the_underlying_call_fails(self, monkeypatch, make_graph_app):
        """warm_cache runs from main.py's lifespan and seed_dashboard.py's
        last step -- a failure there (e.g. a cold Neon compute) must never
        prevent the app from starting or the seed script from finishing.
        """

        async def boom(limit=500, pipeline_env=None):
            raise RuntimeError("simulated Postgres outage")

        monkeypatch.setattr(analytics_module.store, "list_run_records", boom)
        graph_app = make_graph_app({})

        await analytics_module.warm_cache(graph_app, "warm-cache-failure")  # must not raise


class TestSummaryEndpoint:
    async def test_counts_and_rate_across_mixed_run_outcomes(self, monkeypatch, make_run_summary):
        runs = [
            make_run_summary(status="clean"),
            make_run_summary(status="healed", approved_by="auto", heal_attempts=1),
            make_run_summary(status="healed", approved_by="human", heal_attempts=1),
            make_run_summary(status="escalated", heal_attempts=0),
        ]
        monkeypatch.setattr("routers.analytics.collect_run_summaries", _async_return(runs))

        result = await summary(graph_app=None)

        assert result["total_runs"] == 4
        assert result["clean_runs"] == 1
        assert result["drifted_runs"] == 3
        assert result["auto_healed"] == 1
        assert result["human_approved"] == 1
        assert result["escalated"] == 1
        assert result["auto_heal_rate"] == pytest.approx(1 / 3, abs=0.001)

    async def test_zero_runs_returns_zeros_not_a_division_error(self, monkeypatch):
        monkeypatch.setattr("routers.analytics.collect_run_summaries", _async_return([]))

        result = await summary(graph_app=None)

        assert result["total_runs"] == 0
        assert result["auto_heal_rate"] == 0.0
        assert result["mean_heal_attempts"] == 0.0
        assert result["attempted_runs"] == 0


class TestConfidenceEndpoint:
    async def test_emits_one_row_per_history_attempt(self, monkeypatch, make_run_summary):
        run = make_run_summary(
            status="healed",
            history=[
                {"attempt_no": 0, "specialist": "spec_rename", "confidence": 0.9, "result": "failed", "approved_by": "auto"},
                {"attempt_no": 1, "specialist": "spec_type", "confidence": 0.9, "result": "passed", "approved_by": "auto"},
            ],
        )
        monkeypatch.setattr("routers.analytics.collect_run_summaries", _async_return([run]))

        rows = await confidence(graph_app=None)

        assert len(rows) == 2
        assert {r["kind"] for r in rows} == {"attempt"}

    async def test_distinguishes_rejected_from_unclassified_zero_history_escalates(self, monkeypatch, make_run_summary):
        """Both are "escalated with no history", but a human rejecting a
        real patch and diagnose never finding a viable class are different
        stories -- the confidence chart must be able to tell them apart.
        """
        rejected = make_run_summary(status="escalated", human_decision="reject", history=[])
        unclassified = make_run_summary(status="escalated", drift_class="unknown", history=[])
        monkeypatch.setattr("routers.analytics.collect_run_summaries", _async_return([rejected, unclassified]))

        rows = await confidence(graph_app=None)

        assert {r["kind"] for r in rows} == {"rejected", "unclassified"}

    async def test_clean_runs_and_still_running_runs_contribute_no_rows(self, monkeypatch, make_run_summary):
        runs = [make_run_summary(status="clean", history=[]), make_run_summary(status="running", history=[])]
        monkeypatch.setattr("routers.analytics.collect_run_summaries", _async_return(runs))

        rows = await confidence(graph_app=None)

        assert rows == []


class TestSpecialistPerformanceEndpoint:
    async def test_success_rate_is_none_for_a_specialist_with_zero_attempts(self, monkeypatch, make_run_summary):
        run = make_run_summary(status="healed", history=[{"specialist": "spec_type", "result": "passed"}])
        monkeypatch.setattr("routers.analytics.collect_run_summaries", _async_return([run]))

        rows = await specialist_performance(graph_app=None)
        by_specialist = {r["specialist"]: r for r in rows}

        assert by_specialist["spec_nullability"]["attempts"] == 0
        assert by_specialist["spec_nullability"]["success_rate"] is None
        assert by_specialist["spec_type"]["success_rate"] == 1.0
