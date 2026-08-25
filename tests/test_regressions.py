"""Regression tests, one per real bug found during this build.

Every test in this file pins a bug that actually shipped and was actually
observed against real data (a real deployed backend, a real seeded
dashboard, a real rejected day6 run) before being fixed — not a
hypothetical edge case invented while writing tests. Each docstring states
what the bug was, how it was found, and why the assertion below is the one
that would have caught it.
"""

import pytest
from routers.analytics import drift_distribution, summary
from routers.runs import _serialize_state
from services.chat_context import build_run_context

pytestmark = pytest.mark.unit


class TestSerializeStateAwaitingApprovalRegression:
    async def test_serialize_state_preserves_synthesized_awaiting_approval(self, make_snapshot):
        """_serialize_state used to build its response as
        `{"awaiting_approval": awaiting, **values, ...}` — with the
        spread LAST. HealState carries its OWN `awaiting_approval` key,
        always written False (see _blank_state and human_approval.py: the
        node never returns at all while genuinely paused, so it can never
        have written True). Spreading `values` after the synthesized key
        let that permanent False silently win over the live, correct
        value on every single response.

        Found 2026-08-25: a seed script's polling loop, which reads this
        exact field to know when to stop polling and act, could never
        detect a real interrupt. ApprovalPanel's entire visibility check
        is `detail?.awaiting_approval` — this meant the human-approval UI
        had never actually rendered for a real interrupted run, in this
        project's history, until `**values` was moved to spread FIRST.

        This test constructs exactly that condition (a values dict with
        awaiting_approval explicitly False, alongside a genuinely pending
        interrupt) and would fail against the old key order.
        """
        snapshot = make_snapshot(
            {
                "status": "running",
                "awaiting_approval": False,  # HealState's own, permanently-False field
                "human_decision": None,
                "proposed_patch": {"renames": {"acct_ref": "customer_id"}},
            },
            interrupted=True,
            interrupt_value={"proposed_patch": {"renames": {"acct_ref": "customer_id"}}, "confidence": 0.4},
            next=("human_approval",),
        )

        result = _serialize_state("run-1", snapshot)

        assert result["awaiting_approval"] is True
        assert result["api_status"] == "awaiting_approval"


class TestRejectedRunShapeRegression:
    async def test_rejected_run_has_empty_history_but_populated_human_decision(
        self, make_graph_app, make_snapshot
    ):
        """A human rejection skips apply_patch and rerun_validate
        entirely — no HealAttempt is ever appended, so `history` is `[]`
        for a rejected run exactly the same way it is for a clean run or
        an unclassifiable (drift_class="unknown") escalate.

        Found while verifying the read-only chat feature against a real
        rejected day6_ambiguous_rename run: services/chat_context.py's
        build_run_context originally omitted human_decision,
        proposed_patch, applied_patch, patch_rationale, and final_message
        entirely, so with an empty history the model had no way to know a
        human had ever reviewed anything — a materially incomplete
        answer to "why did this escalate?", not merely a wrong one. The
        confidence chart (routers/analytics.py's `confidence` endpoint)
        depends on the same shape to distinguish a rejection from an
        unknown-classified escalate (see `kind: "rejected"` there).
        """
        run_id = "rejected-run"
        rejected_values = {
            "status": "escalated",
            "history": [],
            "human_decision": "reject",
            "human_note": "not convinced by the mapping",
            "proposed_patch": {"renames": {"acct_ref": "customer_id"}},
            "confidence": 0.4,
            "drift_class": "rename",
            "diagnosis": "possible rename, low confidence",
            "final_message": "escalated -- human rejected the proposed patch",
            "heal_attempts": 0,
            "source_path": "data/sources/day6_ambiguous_rename.csv",
            "assertion_failures": [],
            "drift_items": [],
        }
        graph_app = make_graph_app({run_id: make_snapshot(rejected_values)})

        context = await build_run_context(run_id, graph_app)

        assert context["history"] == []
        assert context["human_decision"] == "reject"
        assert context["proposed_patch"] == {"renames": {"acct_ref": "customer_id"}}
        assert context["final_message"] == "escalated -- human rejected the proposed patch"


class TestDriftDistributionRegressions:
    """routers/analytics.py's drift_distribution — two independent bugs
    found while cross-checking the dashboard's drift-distribution bar
    against a direct query of the same seeded data.
    """

    async def test_buckets_by_run_outcome_not_attempt_result(self, monkeypatch, make_run_summary):
        """day4_combo's rename attempt reports result="failed" in
        `history` — not because the rename patch was wrong, but because a
        SECOND, unrelated drift (type) was still present, so the pipeline
        as a whole hadn't passed yet on that rerun. The run still heals,
        two attempts later. An earlier version of this endpoint credited
        each attempt by its OWN rerun_validate result, which counted that
        rename attempt as "escalated" and told a false story ("most
        renames escalate") flatly contradicted by the runs table sitting
        right next to it on the same dashboard.

        A run must be credited by its FINAL outcome (r["status"]), not by
        any individual attempt's transient result.
        """
        combo_run = make_run_summary(
            status="healed",  # the run heals overall
            history=[
                {"specialist": "spec_rename", "result": "failed", "confidence": 0.9},  # unrelated drift still present
                {"specialist": "spec_type", "result": "passed", "confidence": 0.9},
            ],
        )
        monkeypatch.setattr("routers.analytics.collect_run_summaries", _async_return([combo_run]))

        result = await drift_distribution(graph_app=None)
        by_class = {row["drift_class"]: row for row in result}

        assert by_class["rename"]["healed"] == 1
        assert by_class["rename"]["escalated"] == 0

    async def test_excludes_non_terminal_runs(self, monkeypatch, make_run_summary):
        """A run still paused at human_approval (status="awaiting_approval",
        no history yet — the approval hasn't been resolved either way)
        was inflating rename's escalated count by 1 purely because it
        hadn't been resolved *yet*, not because it had actually escalated.
        Only "healed" and "escalated" are terminal outcomes; anything else
        must be skipped entirely, not defaulted into either bucket.
        """
        paused_run = make_run_summary(status="awaiting_approval", drift_class="rename", history=[])
        monkeypatch.setattr("routers.analytics.collect_run_summaries", _async_return([paused_run]))

        result = await drift_distribution(graph_app=None)
        by_class = {row["drift_class"]: row for row in result}

        assert by_class["rename"]["count"] == 0
        assert by_class["rename"]["escalated"] == 0


class TestMeanHealAttemptsRegression:
    async def test_excludes_runs_with_no_attempts_from_the_denominator(self, monkeypatch, make_run_summary):
        """mean_heal_attempts originally divided the total attempt count
        by drifted_runs — which also includes runs that never reached a
        specialist at all (an immediate unknown-classified escalate, or a
        human rejection, both 0 attempts by construction). A successful
        heal is >=1 attempt by definition, so that denominator dragged
        the mean below 1.0 and made it read as nonsensical rather than
        "most drifts heal in one pass, combos take two" — the real
        seeded-data numbers this was found against were 16 total attempts
        over 12 attempted runs (mean 1.33), reported instead as 0.89 by
        dividing by drifted_runs.

        This test constructs a mix where the two possible denominators
        give VISIBLY different answers (one below 1.0, one at a sane
        value >= 1.0), so a regression back to drifted_runs would fail
        loudly rather than by a rounding difference.
        """
        runs = [
            make_run_summary(status="escalated", drift_class="unknown", heal_attempts=0, history=[]),
            make_run_summary(status="escalated", drift_class="unknown", heal_attempts=0, history=[]),
            make_run_summary(status="escalated", human_decision="reject", heal_attempts=0, history=[]),
            make_run_summary(status="healed", heal_attempts=1, history=[{"specialist": "spec_type", "result": "passed"}]),
            make_run_summary(status="healed", heal_attempts=1, history=[{"specialist": "spec_type", "result": "passed"}]),
            make_run_summary(
                status="healed",
                heal_attempts=2,
                history=[
                    {"specialist": "spec_rename", "result": "failed"},
                    {"specialist": "spec_type", "result": "passed"},
                ],
            ),
        ]
        monkeypatch.setattr("routers.analytics.collect_run_summaries", _async_return(runs))

        result = await summary(graph_app=None)

        # Wrong (old) denominator: 4 total attempts / 5 drifted runs = 0.8.
        # Correct denominator: 4 total attempts / 3 attempted runs = 1.33.
        assert result["attempted_runs"] == 3
        assert result["mean_heal_attempts"] == pytest.approx(1.33, abs=0.01)
        assert result["mean_heal_attempts"] >= 1.0


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn
