"""routers/runs.py's `_serialize_state` — the one function translating a raw
LangGraph StateSnapshot into the JSON shape the dashboard actually consumes.
Tested via conftest.py's make_snapshot fixture (a fake exposing exactly the
3 attributes this function reads: .values, .tasks, .next), so these
contract tests need neither a real checkpointer nor a real interrupted run.

The historical dict-spread-order bug this function used to have has its own
dedicated pin in test_regressions.py; the tests here are about the general
API-shape contract, not that one bug specifically.
"""

import pytest
from routers.runs import _serialize_state

pytestmark = pytest.mark.unit


class TestNonInterruptedStates:
    @pytest.mark.parametrize(
        "status",
        [
            pytest.param("running", id="running"),
            pytest.param("healed", id="healed"),
            pytest.param("escalated", id="escalated"),
            pytest.param("clean", id="clean"),
        ],
    )
    def test_api_status_passes_through_when_nothing_is_paused(self, make_snapshot, status):
        """With no live interrupt, api_status is simply whatever HealState's
        own `status` field says -- the synthesized awaiting_approval logic
        only overrides that when the checkpointer reports a genuine pause.
        """
        snapshot = make_snapshot({"status": status}, next=())
        result = _serialize_state("run-1", snapshot)

        assert result["api_status"] == status
        assert result["awaiting_approval"] is False
        assert result["interrupt"] is None
        assert result["next"] == []

    def test_missing_status_field_defaults_to_running(self, make_snapshot):
        """A snapshot whose values dict has no "status" key at all (the
        moment right after START, before run_pipeline has returned) must
        default to "running", not KeyError.
        """
        snapshot = make_snapshot({}, next=("run_pipeline",))
        result = _serialize_state("run-1", snapshot)

        assert result["api_status"] == "running"


class TestInterruptedState:
    def test_awaiting_approval_reflects_the_live_interrupt_not_the_stale_field(self, make_snapshot):
        """HealState's own `awaiting_approval` field is only ever written
        False (see _blank_state and human_approval.py) -- it can never be
        True by the time a client reads it, since human_approval doesn't
        return at all while genuinely paused. The checkpointer's pending
        interrupt is therefore the ONLY correct source for this field; a
        values dict claiming awaiting_approval=False alongside a real
        pending interrupt must still surface as awaiting_approval=True.
        """
        snapshot = make_snapshot(
            {"status": "running", "awaiting_approval": False},
            interrupted=True,
            interrupt_value={"proposed_patch": {"renames": {"a": "b"}}, "confidence": 0.4},
            next=("human_approval",),
        )
        result = _serialize_state("run-1", snapshot)

        assert result["api_status"] == "awaiting_approval"
        assert result["awaiting_approval"] is True
        assert result["interrupt"] == {"proposed_patch": {"renames": {"a": "b"}}, "confidence": 0.4}
        assert result["next"] == ["human_approval"]

    def test_underlying_status_field_is_ignored_while_paused(self, make_snapshot):
        """api_status must reflect the pause even if HealState's own
        `status` field still says "running" underneath it -- a paused run
        is awaiting_approval, full stop, regardless of what the last node
        to actually return happened to write.
        """
        snapshot = make_snapshot(
            {"status": "running"}, interrupted=True, interrupt_value={"confidence": 0.5}, next=("human_approval",)
        )
        result = _serialize_state("run-1", snapshot)

        assert result["api_status"] == "awaiting_approval"


class TestFieldContract:
    def test_run_id_parameter_wins_over_any_run_id_inside_values(self, make_snapshot):
        """HealState carries its own `run_id` key. The function parameter
        must be the one that ends up in the response -- the same
        "explicit key must not be silently overwritten by **values"
        property the awaiting_approval regression turned on, checked here
        for the OTHER field that could collide the same way.
        """
        snapshot = make_snapshot({"run_id": "stale-value-from-state", "status": "healed"})
        result = _serialize_state("authoritative-run-id", snapshot)

        assert result["run_id"] == "authoritative-run-id"

    def test_mapping_audit_defaults_to_empty_list(self, make_snapshot):
        """Callers that don't have an audit trail to pass (or haven't
        fetched one) must get [], not None -- the frontend indexes into
        this list directly.
        """
        snapshot = make_snapshot({"status": "clean"})
        result = _serialize_state("run-1", snapshot)

        assert result["mapping_audit"] == []

    def test_mapping_audit_passes_through_unchanged_when_provided(self, make_snapshot):
        snapshot = make_snapshot({"status": "healed"})
        audit = [{"id": 1, "updated_by": "agent:attempt-1"}]
        result = _serialize_state("run-1", snapshot, mapping_audit=audit)

        assert result["mapping_audit"] is audit

    def test_empty_values_still_produces_a_well_formed_response(self, make_snapshot):
        """snapshot.values can be falsy (an empty mappingproxy) for a
        thread that technically exists in the checkpointer but has no
        recorded state yet. _serialize_state must not KeyError on that --
        the 404-for-truly-missing-run decision belongs to get_run, one
        layer up, which checks this before calling here.
        """
        snapshot = make_snapshot({})
        result = _serialize_state("run-1", snapshot)

        assert result["run_id"] == "run-1"
        assert result["api_status"] == "running"
        assert result["awaiting_approval"] is False
