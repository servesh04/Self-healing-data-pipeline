"""graph/routing.py — the 5 conditional-edge routers, unit-tested in
isolation from the graph they wire together.

Every router here is a pure function (HealState -> str); none of them touch
the network, the LLM, or Postgres, so there is no mocking to do at all --
just constructing states and asserting the returned edge name. The boundary
cases (confidence exactly at the threshold, heal_attempts exactly at the
cap) are the point of this file: they are the inputs most likely to regress
silently if CONFIDENCE_THRESHOLD or MAX_HEAL_ATTEMPTS's comparison operator
ever changes from >= to >, or vice versa.
"""

import pytest
from graph.routing import (
    route_after_confidence,
    route_after_diagnose,
    route_after_human_approval,
    route_after_rerun,
    route_after_run,
)
from graph.state import CONFIDENCE_THRESHOLD, MAX_HEAL_ATTEMPTS

pytestmark = pytest.mark.unit


class TestRouteAfterRun:
    @pytest.mark.parametrize(
        "assertions_passed, expected",
        [
            pytest.param(True, "commit_report", id="clean-first-pass-skips-healing"),
            pytest.param(False, "profile_source", id="drift-detected-enters-heal-path"),
        ],
    )
    def test_routes_on_first_pipeline_result(self, make_heal_state, assertions_passed, expected):
        """A run that passes on the very first pipeline call never enters
        the heal path at all -- no diagnose, no specialist, no patch. Only
        a failing first pass is allowed to reach profile_source.
        """
        state = make_heal_state(assertions_passed=assertions_passed)
        assert route_after_run(state) == expected


class TestRouteAfterDiagnose:
    @pytest.mark.parametrize(
        "drift_class, expected",
        [
            pytest.param("rename", "spec_rename", id="rename-dispatches-to-its-specialist"),
            pytest.param("type", "spec_type", id="type-dispatches-to-its-specialist"),
            pytest.param("nullability", "spec_nullability", id="nullability-dispatches-to-its-specialist"),
            pytest.param("unknown", "escalate", id="unknown-escalates-never-a-specialist"),
            pytest.param(None, "escalate", id="missing-classification-escalates-defensively"),
        ],
    )
    def test_dispatches_by_drift_class(self, make_heal_state, drift_class, expected):
        """diagnose's classification is the only thing this router reads.
        A viable class goes to exactly one specialist; anything else --
        including the literal "unknown" diagnose returns when it can't
        classify at all, and a hypothetical missing value -- escalates
        without ever touching a specialist node.
        """
        state = make_heal_state(drift_class=drift_class)
        assert route_after_diagnose(state) == expected


class TestRouteAfterConfidence:
    @pytest.mark.parametrize(
        "confidence, expected",
        [
            pytest.param(0.95, "apply_patch", id="high-confidence-auto-applies"),
            pytest.param(
                CONFIDENCE_THRESHOLD,
                "apply_patch",
                id="exactly-at-threshold-wins-the-auto-apply-side",
            ),
            pytest.param(0.74, "human_approval", id="just-below-threshold-needs-a-human"),
            pytest.param(0.3, "human_approval", id="low-confidence-needs-a-human"),
            pytest.param(
                None,
                "human_approval",
                id="missing-confidence-defaults-to-zero-not-auto-apply",
            ),
        ],
    )
    def test_routes_on_confidence_threshold(self, make_heal_state, confidence, expected):
        """CONFIDENCE_THRESHOLD (0.75) is a >= comparison, not >: a
        diagnosis landing exactly on the threshold must auto-apply, since
        that is the value the specialist's own calibration is tuned
        against. A missing confidence (state["confidence"] is None) must
        NOT be treated as maximally confident -- `or 0.0` inside the router
        is what prevents a None from silently auto-applying an unvetted
        patch.
        """
        state = make_heal_state(confidence=confidence)
        assert route_after_confidence(state) == expected


class TestRouteAfterRerun:
    def test_passing_rerun_always_commits_regardless_of_attempt_count(self, make_heal_state):
        """A rerun that now passes goes straight to commit_report even if
        it took multiple attempts to get there -- heal_attempts is not
        consulted at all once assertions_passed is True.
        """
        state = make_heal_state(assertions_passed=True, heal_attempts=2)
        assert route_after_rerun(state) == "commit_report"

    @pytest.mark.parametrize(
        "heal_attempts, expected",
        [
            pytest.param(0, "diff_contract", id="first-failed-attempt-loops-back"),
            pytest.param(
                MAX_HEAL_ATTEMPTS - 1,
                "diff_contract",
                id="cap-not-yet-reached-still-loops",
            ),
            pytest.param(
                MAX_HEAL_ATTEMPTS,
                "escalate",
                id="cap-reached-escalates-not-another-loop",
            ),
            pytest.param(
                MAX_HEAL_ATTEMPTS + 1,
                "escalate",
                id="past-the-cap-still-escalates-not-a-crash",
            ),
        ],
    )
    def test_failing_rerun_loops_until_the_cap_then_escalates(
        self, make_heal_state, heal_attempts, expected
    ):
        """The heal cycle itself: a failing rerun loops back to
        diff_contract as long as heal_attempts is still under
        MAX_HEAL_ATTEMPTS (3), and escalates the moment it is not -- the
        boundary at exactly the cap is what actually stops the cycle, so it
        is the one value most likely to silently regress if the comparison
        operator here ever changed from >= to >.
        """
        state = make_heal_state(assertions_passed=False, heal_attempts=heal_attempts)
        assert route_after_rerun(state) == expected


class TestRouteAfterHumanApproval:
    @pytest.mark.parametrize(
        "human_decision, expected",
        [
            pytest.param("approve", "apply_patch", id="approve-reaches-apply-patch"),
            pytest.param("reject", "escalate", id="reject-escalates"),
            pytest.param(None, "escalate", id="no-decision-recorded-escalates-defensively"),
        ],
    )
    def test_dispatches_the_human_decision(self, make_heal_state, human_decision, expected):
        """This is the one router dispatching the HUMAN's decision rather
        than the pipeline's own state. Only an explicit "approve" reaches
        apply_patch; a rejection -- or, defensively, anything else --
        escalates. This router call is the only thing standing between a
        rejection and apply_patch: at the router level, "reject" simply
        never appears in the returned-string set that maps to
        "apply_patch", so apply_patch is not reachable through this edge
        for a rejected run. (The compiled-graph-level proof that apply_patch
        truly never fires for a rejected run -- not just that this router
        never names it -- lives in test_graph_structure.py.)
        """
        state = make_heal_state(human_decision=human_decision)
        assert route_after_human_approval(state) == expected
