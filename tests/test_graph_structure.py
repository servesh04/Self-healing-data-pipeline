"""graph/build.py's compiled topology: all 13 nodes present, the heal-cycle
edge actually exists, and — the part a purely static edge check can't
prove — that cycle, the MAX_HEAL_ATTEMPTS cap, the human-approval fork, and
the `history` reducer all behave correctly when the graph is actually run.

Every node here is either the real, already-pure node (human_approval,
escalate, commit_report — no LLM, no Postgres, nothing to fake) or a
deterministic stub swapped in via conftest.py's build_stub_graph, which
monkeypatches graph.build's node references and then calls the REAL
build_graph(). The topology, the conditional-edge wiring, and every router
in graph/routing.py are exercised exactly as written — nothing here is a
hand-reconstructed parallel graph that could silently drift from the real
one.
"""

import pytest
from graph.schemas import (
    DiagnoseOutput,
    NullabilitySpecialistOutput,
    RenameSpecialistOutput,
    TypeSpecialistOutput,
    normalize_llm_json,
)
from graph.state import MAX_HEAL_ATTEMPTS, RECURSION_LIMIT
from langgraph.types import Command
from pydantic import ValidationError

pytestmark = pytest.mark.integration

ALL_NODE_NAMES = {
    "run_pipeline",
    "profile_source",
    "diff_contract",
    "diagnose",
    "spec_rename",
    "spec_type",
    "spec_nullability",
    "propose_patch",
    "human_approval",
    "apply_patch",
    "rerun_validate",
    "escalate",
    "commit_report",
}


def _counting(fn):
    """Wrap a node body so a test can assert how many times it actually
    fired -- e.g. proving apply_patch is never reached on a rejected run,
    not just that route_after_human_approval doesn't name it (that's
    test_routing.py's job; this is the graph actually enforcing it).
    """
    calls = {"n": 0}

    def wrapped(state):
        calls["n"] += 1
        return fn(state)

    return wrapped, calls


def _failing_then_passing_rerun_validate(fail_times: int):
    """A rerun_validate stub that fails the first `fail_times` visits, then
    passes -- drives the heal cycle a controlled number of iterations so
    heal_attempts/history can be asserted exactly, not just "some subset".
    """
    seen = {"n": 0}

    def rerun_validate(state):
        seen["n"] += 1
        passed = seen["n"] > fail_times
        attempt = {
            "attempt_no": state["heal_attempts"],
            "drift_summary": state.get("diagnosis") or "",
            "specialist": f"spec_{state.get('drift_class')}",
            "patch": state.get("applied_patch") or {},
            "confidence": state.get("confidence") or 0.0,
            "approved_by": "human" if state.get("human_decision") else "auto",
            "result": "passed" if passed else "failed",
            "assertion_failures": [] if passed else ["stub: still drifted"],
        }
        return {
            "assertions_passed": passed,
            "assertion_failures": attempt["assertion_failures"],
            "assertion_failure_details": [],
            "rows_out": 10,
            "history": [attempt],
        }

    return rerun_validate


def _blank_state(heal_state_factory) -> dict:
    return heal_state_factory(assertions_passed=False)  # forces entry into the heal path


class TestTopology:
    """Static shape of the compiled graph -- no execution needed."""

    def test_all_thirteen_nodes_are_registered(self, build_stub_graph):
        """Every node ARCHITECTURE.md's topology names must actually be
        wired into the compiled graph, not just defined as a function
        somewhere under graph/nodes/.
        """
        compiled = build_stub_graph()
        node_names = set(compiled.get_graph().nodes.keys()) - {"__start__", "__end__"}
        assert node_names == ALL_NODE_NAMES

    def test_heal_cycle_edge_exists_from_rerun_validate_to_diff_contract(self, build_stub_graph):
        """THE HEAL CYCLE, structurally: rerun_validate must have a
        conditional edge whose target is diff_contract. Without this edge
        the cap in route_after_rerun would be unreachable code -- there
        would be nowhere to loop back to.
        """
        compiled = build_stub_graph()
        edges = compiled.get_graph().edges
        assert any(e.source == "rerun_validate" and e.target == "diff_contract" for e in edges)


class TestHealCycleExecution:
    """Actually running the stub graph -- this is what a static edge check
    cannot prove: that the cycle fires the right number of times, that
    heal_attempts and history stay in lockstep with it, and that it stops.
    """

    @pytest.mark.parametrize(
        "fail_times",
        [
            pytest.param(0, id="heals-on-first-attempt"),
            pytest.param(1, id="heals-on-second-attempt-one-loop"),
            pytest.param(MAX_HEAL_ATTEMPTS - 1, id="heals-on-final-allowed-attempt"),
        ],
    )
    def test_heal_attempts_and_history_track_the_number_of_loop_iterations(
        self, build_stub_graph, make_heal_state, fail_times
    ):
        """Each failed rerun_validate must add exactly one HealAttempt via
        the `Annotated[list, operator.add]` reducer (verified accumulating
        across a real resume before this suite was written -- see
        conftest.py's build_stub_graph docstring) and one heal_attempts
        increment, so the two numbers must match by construction.
        """
        rerun_stub = _failing_then_passing_rerun_validate(fail_times)
        compiled = build_stub_graph(overrides={"rerun_validate": rerun_stub})
        cfg = {"configurable": {"thread_id": f"heal-{fail_times}"}, "recursion_limit": RECURSION_LIMIT}

        final = compiled.invoke(_blank_state(make_heal_state), config=cfg)

        assert final["status"] == "healed"
        assert final["heal_attempts"] == fail_times + 1
        assert len(final["history"]) == fail_times + 1

    def test_cap_reached_escalates_and_apply_patch_fires_exactly_max_times(
        self, build_stub_graph, make_heal_state
    ):
        """A rerun that never passes must stop looping the moment
        heal_attempts hits MAX_HEAL_ATTEMPTS (3), not one iteration later
        or earlier -- and apply_patch, the only place heal_attempts is
        allowed to increment, must have fired exactly that many times.
        """
        always_fails = _failing_then_passing_rerun_validate(fail_times=10**6)
        apply_patch_stub, apply_calls = _counting(
            lambda state: {
                "heal_attempts": state["heal_attempts"] + 1,
                "applied_patch": state.get("proposed_patch") or {},
                "awaiting_approval": False,
            }
        )
        compiled = build_stub_graph(
            overrides={"rerun_validate": always_fails, "apply_patch": apply_patch_stub}
        )
        cfg = {"configurable": {"thread_id": "cap-reached"}, "recursion_limit": RECURSION_LIMIT}

        final = compiled.invoke(_blank_state(make_heal_state), config=cfg)

        assert final["status"] == "escalated"
        assert final["heal_attempts"] == MAX_HEAL_ATTEMPTS
        assert apply_calls["n"] == MAX_HEAL_ATTEMPTS
        assert len(final["history"]) == MAX_HEAL_ATTEMPTS


class TestHumanApprovalFork:
    """Low-confidence diagnoses must actually pause at human_approval (a
    real interrupt(), not a stub) and the human's decision must be the
    only thing that determines whether apply_patch ever runs.
    """

    def _low_confidence_graph(self, build_stub_graph, overrides=None):
        apply_patch_stub, apply_calls = _counting(
            lambda state: {
                "heal_attempts": state["heal_attempts"] + 1,
                "applied_patch": state.get("proposed_patch") or {},
                "awaiting_approval": False,
            }
        )
        stub_overrides = {
            "diagnose": lambda state: {
                "drift_class": "rename",
                "diagnosis": "stub: unsure",
                "confidence": 0.3,  # below CONFIDENCE_THRESHOLD -> human_approval
            },
            "apply_patch": apply_patch_stub,
            **(overrides or {}),
        }
        return build_stub_graph(overrides=stub_overrides), apply_calls

    def test_low_confidence_pauses_at_human_approval(self, build_stub_graph, make_heal_state):
        """The pause itself: a real interrupt(), visible on the state
        snapshot exactly the way routers/runs.py's _serialize_state reads
        it (snapshot.next / snapshot.tasks[].interrupts) -- not a stand-in
        for that mechanism.
        """
        compiled, _ = self._low_confidence_graph(build_stub_graph)
        cfg = {"configurable": {"thread_id": "pause"}, "recursion_limit": RECURSION_LIMIT}

        compiled.invoke(_blank_state(make_heal_state), config=cfg)
        snapshot = compiled.get_state(cfg)

        assert snapshot.next == ("human_approval",)
        interrupts = [i.value for task in snapshot.tasks for i in task.interrupts]
        assert len(interrupts) == 1
        assert interrupts[0]["confidence"] == 0.3

    def test_reject_escalates_and_apply_patch_never_fires(self, build_stub_graph, make_heal_state):
        """The graph-level version of test_routing.py's router-only claim:
        a rejected run must never actually invoke apply_patch, not merely
        never be routed there by name.
        """
        compiled, apply_calls = self._low_confidence_graph(build_stub_graph)
        cfg = {"configurable": {"thread_id": "reject"}, "recursion_limit": RECURSION_LIMIT}

        compiled.invoke(_blank_state(make_heal_state), config=cfg)
        final = compiled.invoke(Command(resume={"decision": "reject", "note": "not convinced"}), config=cfg)

        assert final["status"] == "escalated"
        assert final["human_decision"] == "reject"
        assert apply_calls["n"] == 0

    def test_approve_reaches_apply_patch_and_heals(self, build_stub_graph, make_heal_state):
        """The other side of the fork: an explicit approval must reach
        apply_patch (and only apply_patch decides heal_attempts), then
        proceed through the same rerun_validate/commit_report path a
        high-confidence auto-apply would.
        """
        compiled, apply_calls = self._low_confidence_graph(build_stub_graph)
        cfg = {"configurable": {"thread_id": "approve"}, "recursion_limit": RECURSION_LIMIT}

        compiled.invoke(_blank_state(make_heal_state), config=cfg)
        final = compiled.invoke(Command(resume={"decision": "approve", "note": None}), config=cfg)

        assert final["status"] == "healed"
        assert final["human_decision"] == "approve"
        assert apply_calls["n"] == 1
        assert final["history"][-1]["approved_by"] == "human"

    def test_bare_string_resume_is_also_accepted(self, build_stub_graph, make_heal_state):
        """human_approval's docstring says it accepts either a bare
        "approve"/"reject" string OR a {"decision": ..., "note": ...}
        dict -- the former is what a plain Command(resume="approve")
        sends, still relevant since it's what the original graph smoke
        test used. Both shapes must resume the run the same way; a
        dict-only resume path would break that backward compatibility
        silently.
        """
        compiled, apply_calls = self._low_confidence_graph(build_stub_graph)
        cfg = {"configurable": {"thread_id": "bare-string-resume"}, "recursion_limit": RECURSION_LIMIT}

        compiled.invoke(_blank_state(make_heal_state), config=cfg)
        final = compiled.invoke(Command(resume="approve"), config=cfg)

        assert final["status"] == "healed"
        assert final["human_decision"] == "approve"
        assert final["human_note"] is None
        assert apply_calls["n"] == 1


class TestUnknownDriftNeverReachesASpecialist:
    def test_unknown_classification_escalates_without_calling_any_specialist(
        self, build_stub_graph, make_heal_state
    ):
        """Mirrors day5_unfixable's real scenario: diagnose returning
        "unknown" must escalate immediately, and none of the 3 specialist
        nodes may fire -- not "fire and get ignored downstream", actually
        never invoked at all.
        """
        rename_stub, rename_calls = _counting(lambda state: {"specialist_output": {}})
        type_stub, type_calls = _counting(lambda state: {"specialist_output": {}})
        null_stub, null_calls = _counting(lambda state: {"specialist_output": {}})
        compiled = build_stub_graph(
            overrides={
                "diagnose": lambda state: {
                    "drift_class": "unknown",
                    "diagnosis": "stub: unclassifiable",
                    "confidence": 0.0,
                },
                "spec_rename": rename_stub,
                "spec_type": type_stub,
                "spec_nullability": null_stub,
            }
        )
        cfg = {"configurable": {"thread_id": "unknown"}, "recursion_limit": RECURSION_LIMIT}

        final = compiled.invoke(_blank_state(make_heal_state), config=cfg)

        assert final["status"] == "escalated"
        assert rename_calls["n"] == type_calls["n"] == null_calls["n"] == 0


class TestLLMOutputSchemas:
    """graph/schemas.py's Pydantic models -- the actual enforcement of "an
    agent proposes config patches only, never Python, and never outside
    its own specialist's section." Every case here hand-constructs the
    dict an LLM call might have returned; none of it touches an LLM, since
    this is testing OUR validation of a shape, not model behavior (see the
    README's note on the LLM-behavior exclusion).
    """

    @pytest.mark.parametrize(
        "raw, expected",
        [
            pytest.param({"a": 1}, [{"a": 1}], id="a-plain-dict-is-returned-as-a-single-candidate"),
            pytest.param([{"a": 1}, {"a": 2}], [{"a": 1}, {"a": 2}], id="a-list-of-dicts-passes-through-unchanged"),
            pytest.param([{"a": 1}, "not-a-dict"], [{"a": 1}], id="non-dict-list-entries-are-dropped"),
            pytest.param("neither a dict nor a list", [], id="anything-else-yields-no-candidates"),
        ],
    )
    def test_normalize_llm_json_handles_groqs_list_wrapping_quirk(self, raw, expected):
        assert normalize_llm_json(raw) == expected

    def test_rename_specialist_output_rejects_a_leaked_casts_key(self):
        """The actual point of catching scope leakage AT the specialist,
        not three nodes later: a rename specialist that also emits a
        `casts` key fails validation right here, by name, instead of
        looking like a small, harmless, unrelated patch fragment riding
        along into propose_patch.
        """
        with pytest.raises(ValidationError):
            RenameSpecialistOutput.model_validate({"renames": {"a": "b"}, "casts": {"c": "d"}})

    def test_type_specialist_output_rejects_an_unregistered_cast_op(self):
        with pytest.raises(ValidationError):
            TypeSpecialistOutput.model_validate({"casts": {"revenue": "eval_arbitrary_python"}})

    def test_nullability_specialist_output_rejects_an_unregistered_op(self):
        with pytest.raises(ValidationError):
            NullabilitySpecialistOutput.model_validate({"null_policy": {"region": "not_a_real_op"}})

    def test_diagnose_output_rejects_a_drift_class_outside_the_declared_set(self):
        """Confidence calibration is the entire escalation story
        (diagnose.py's docstring) -- but only among the 4 declared
        classes. A model inventing a 5th must fail validation and fall
        back to diagnose's own unknown/zero-confidence default, not
        silently propagate a class route_after_diagnose has never heard
        of.
        """
        with pytest.raises(ValidationError):
            DiagnoseOutput.model_validate({"drift_class": "made_up_class", "diagnosis": "x", "confidence": 0.5})
