"""services/chat_context.py — payload assembly, truncation, and the token
budget check for the read-only run-data chat feature. Nothing here touches
an LLM: this file is entirely about what gets INTO the prompt, not what a
model does with it (see the README's note on why LLM node behavior itself
is not unit-tested).
"""

import logging

import pytest
import services.chat_context as chat_context

pytestmark = pytest.mark.unit


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn


class TestEstimateTokens:
    def test_estimates_roughly_one_token_per_four_characters(self):
        assert chat_context.estimate_tokens("a" * 400) == 100

    def test_non_string_payloads_are_json_encoded_first(self):
        payload = {"a": 1, "b": [1, 2, 3]}
        expected = len(__import__("json").dumps(payload)) // 4
        assert chat_context.estimate_tokens(payload) == expected


class TestTruncatePatch:
    def test_a_small_patch_is_left_untouched(self):
        patch = {"renames": {"cust_id": "customer_id"}}
        result = chat_context._truncate_patch(patch)
        assert result == __import__("json").dumps(patch)

    def test_a_patch_over_the_char_limit_is_truncated_with_a_length_note(self):
        huge_patch = {"renames": {f"col_{i}": f"target_{i}" for i in range(200)}}
        result = chat_context._truncate_patch(huge_patch)

        assert len(result) < len(__import__("json").dumps(huge_patch))
        assert result.endswith("chars total)")
        assert "...(truncated," in result

    def test_none_is_treated_as_an_empty_patch(self):
        assert chat_context._truncate_patch(None) == "{}"


class TestTruncateFailures:
    def test_a_short_failure_list_is_left_untouched(self):
        failures = ["a: missing_column", "b: dtype_mismatch"]
        assert chat_context._truncate_failures(failures) == failures

    def test_a_long_failure_list_is_capped_with_a_remainder_count(self):
        failures = [f"failure {i}" for i in range(10)]
        result = chat_context._truncate_failures(failures)

        assert len(result) == chat_context.MAX_ASSERTION_FAILURES_PER_ATTEMPT + 1
        assert result[:-1] == failures[: chat_context.MAX_ASSERTION_FAILURES_PER_ATTEMPT]
        assert result[-1] == "+7 more"

    def test_none_is_treated_as_an_empty_list(self):
        assert chat_context._truncate_failures(None) == []


class TestCondenseHistory:
    def test_keeps_only_the_fields_the_chat_prompt_needs_per_attempt(self):
        history = [
            {
                "attempt_no": 0,
                "specialist": "spec_rename",
                "patch": {"renames": {"a": "b"}},
                "confidence": 0.9,
                "result": "passed",
                "approved_by": "auto",
                "assertion_failures": [],
                "drift_summary": "not part of the condensed shape",
            }
        ]
        condensed = chat_context._condense_history(history)

        assert condensed == [
            {
                "attempt_no": 0,
                "specialist": "spec_rename",
                "patch": '{"renames": {"a": "b"}}',
                "confidence": 0.9,
                "result": "passed",
                "approved_by": "auto",
                "assertion_failures": [],
            }
        ]


class TestBuildRunContext:
    async def test_returns_none_when_the_run_does_not_exist(self, make_graph_app):
        graph_app = make_graph_app({})  # no snapshot registered for any run_id
        assert await chat_context.build_run_context("missing-run", graph_app) is None

    async def test_includes_the_fields_a_rejected_or_escalated_run_depends_on(
        self, make_graph_app, make_snapshot
    ):
        """The specific 5 fields added after real-data testing exposed the
        gap for a rejected run (see test_regressions.py for the full
        historical account) must be part of the general contract, not an
        exception -- every run's context includes them, populated or not.
        """
        values = {
            "status": "escalated",
            "source_path": "data/sources/day6_ambiguous_rename.csv",
            "drift_class": "rename",
            "diagnosis": "possible rename, low confidence",
            "confidence": 0.4,
            "heal_attempts": 0,
            "history": [],
            "human_decision": "reject",
            "human_note": "not convinced",
            "proposed_patch": {"renames": {"acct_ref": "customer_id"}},
            "applied_patch": None,
            "patch_rationale": "acct_ref looks like a renamed customer_id",
            "final_message": "escalated -- human rejected the proposed patch",
        }
        graph_app = make_graph_app({"r1": make_snapshot(values)})

        context = await chat_context.build_run_context("r1", graph_app)

        assert context["dataset"] == "day6_ambiguous_rename.csv"
        assert context["human_decision"] == "reject"
        assert context["proposed_patch"] == {"renames": {"acct_ref": "customer_id"}}
        assert context["patch_rationale"] == "acct_ref looks like a renamed customer_id"
        assert context["final_message"] == "escalated -- human rejected the proposed patch"

    async def test_status_reflects_a_live_interrupt_over_the_stale_state_field(
        self, make_graph_app, make_snapshot
    ):
        values = {"status": "running", "source_path": "data/sources/day6_ambiguous_rename.csv", "heal_attempts": 0, "history": []}
        graph_app = make_graph_app(
            {"r1": make_snapshot(values, interrupted=True, interrupt_value={"confidence": 0.4}, next=("human_approval",))}
        )

        context = await chat_context.build_run_context("r1", graph_app)

        assert context["status"] == "awaiting_approval"

    async def test_logs_a_warning_when_the_context_exceeds_the_token_cap(
        self, monkeypatch, make_graph_app, make_snapshot, caplog
    ):
        """A context that's genuinely too large must be logged, not
        silently sent -- this is the one observability hook this module
        has for "something assembled a bigger prompt than expected".
        Lowering the cap itself (rather than constructing a real 30k-token
        state) keeps this test fast while exercising the real comparison.
        """
        monkeypatch.setattr(chat_context, "MAX_CONTEXT_TOKENS", 1)
        values = {"status": "clean", "source_path": "data/sources/day1_clean.csv", "heal_attempts": 0, "history": []}
        graph_app = make_graph_app({"r1": make_snapshot(values)})

        with caplog.at_level(logging.WARNING):
            await chat_context.build_run_context("r1", graph_app)

        assert any("estimated at" in record.message for record in caplog.records)


class TestBuildGlobalContext:
    def _patch_run_summaries(self, monkeypatch, runs):
        # Patched in BOTH namespaces that imported it: routers.analytics's
        # copy (used internally by analytics_summary/drift_distribution/
        # specialist_performance/confidence) and services.chat_context's
        # own copy (used directly for the `runs` list). `from X import Y`
        # binds a separate name in each importing module -- patching the
        # origin module's attribute after that point would not reach
        # either of these already-bound copies.
        monkeypatch.setattr("routers.analytics.collect_run_summaries", _async_return(runs))
        monkeypatch.setattr("services.chat_context.collect_run_summaries", _async_return(runs))

    async def test_assembles_aggregates_and_a_lean_run_list(self, monkeypatch, make_run_summary):
        runs = [
            make_run_summary(
                run_id="r1",
                status="healed",
                heal_attempts=1,
                history=[
                    {
                        "attempt_no": 0,
                        "specialist": "spec_type",
                        "result": "passed",
                        "confidence": 0.9,
                        "approved_by": "auto",
                    }
                ],
            ),
            make_run_summary(run_id="r2", status="escalated", drift_class="unknown", history=[]),
        ]
        self._patch_run_summaries(monkeypatch, runs)

        context = await chat_context.build_global_context(graph_app=None)

        assert context["summary"]["total_runs"] == 2
        assert {r["run_id"] for r in context["runs"]} == {"r1", "r2"}
        # Lean projection: no per-attempt `history` blob in the run list --
        # that granularity already lives in confidence_rows.
        assert all("history" not in r for r in context["runs"])

    async def test_status_and_dataset_filters_narrow_the_run_list_only(self, monkeypatch, make_run_summary):
        """Filters apply to the `runs` list a question might ask about by
        name -- the aggregates stay computed over EVERYTHING, since a
        filtered summary would silently contradict the dashboard's own
        unfiltered KPI strip.
        """
        runs = [
            make_run_summary(run_id="r1", source_path="data/sources/day4_combo.csv", status="healed"),
            make_run_summary(run_id="r2", source_path="data/sources/day5_unfixable.csv", status="escalated"),
        ]
        self._patch_run_summaries(monkeypatch, runs)

        context = await chat_context.build_global_context(graph_app=None, status="escalated")

        assert [r["run_id"] for r in context["runs"]] == ["r2"]
        assert context["summary"]["total_runs"] == 2  # unfiltered

    async def test_runs_are_sorted_most_recent_first(self, monkeypatch, make_run_summary):
        from datetime import datetime

        older = make_run_summary(run_id="older", created_at=datetime(2024, 1, 1))
        newer = make_run_summary(run_id="newer", created_at=datetime(2024, 6, 1))
        self._patch_run_summaries(monkeypatch, [older, newer])

        context = await chat_context.build_global_context(graph_app=None)

        assert [r["run_id"] for r in context["runs"]] == ["newer", "older"]
