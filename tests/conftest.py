"""Shared fixtures for the offline pytest suite.

Everything here runs with zero network access: no Postgres, no Groq, no
LangSmith. Fixtures either construct plain data (HealState dicts, fault CSV
paths) or exist to keep a real module importable without a live backend --
see the env-var bootstrap below.

Grows incrementally alongside the test files that need each fixture, rather
than declaring everything up front; see each fixture's docstring for which
test module introduced it.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
DATA_DIR = REPO_ROOT / "data" / "sources"

# pytest.ini's `pythonpath = backend` already does this for a normal `pytest`
# invocation; this is a defensive fallback for anyone importing conftest.py
# directly or running from an unusual cwd, since every backend module uses
# bare top-level imports (`graph.state`, `pipeline.contract`, ...) that only
# resolve with backend/ itself on sys.path.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# config.Settings requires DATABASE_URL/SHARED_TOKEN with no default -- and,
# left unset, pydantic-settings falls back to reading the repo's own .env
# (real Neon credentials) the moment any code path under test calls
# get_settings(). Nothing in this suite is supposed to reach that far, but a
# test process should never be ABLE to touch a real database or echo a real
# token into a failure message just because a fixture forgot to stub
# something. setdefault, not assignment: a CI environment that sets its own
# values keeps them.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("SHARED_TOKEN", "test-token")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:5173")

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from graph.state import HealState  # noqa: E402


@pytest.fixture(autouse=True)
def _langsmith_disabled_during_tests():
    """LangSmith tracing must never activate while this suite runs, on any
    machine -- including a developer's shell with a real LANGSMITH_API_KEY
    and LANGSMITH_TRACING=true already exported for normal (non-test) runs.
    Forced off for every test, then restored, rather than asserted-and-left:
    a graded suite has to be reproducible on a machine that already has
    tracing configured, not just on a clean one.
    """
    prev = os.environ.get("LANGSMITH_TRACING")
    os.environ["LANGSMITH_TRACING"] = "false"
    yield
    if prev is None:
        os.environ.pop("LANGSMITH_TRACING", None)
    else:
        os.environ["LANGSMITH_TRACING"] = prev


@pytest.fixture(scope="session")
def contract():
    """The real, unmocked Contract built from config/contract.yaml --
    exactly what run_pipeline validates every dataset against in
    production. Session-scoped: it's read-only and pipeline.contract's own
    @lru_cache already treats it as a singleton, so re-fetching it per
    test would just add fixture overhead for the same object.

    Used by test_pipeline.py.
    """
    from pipeline.contract import load_contract

    return load_contract()


@pytest.fixture
def fault_datasets() -> dict[str, Path]:
    """Path to each of the 6 real fault CSVs in data/sources/ -- the same
    files the deployed app seeds its demo runs from, not synthetic
    stand-ins. Keyed by stem (e.g. "day4_combo") for readable test ids.
    Used by test_pipeline.py.
    """
    return {p.stem: p for p in sorted(DATA_DIR.glob("*.csv"))}


@pytest.fixture
def make_heal_state():
    """Factory for a complete HealState dict with clean-run defaults
    (assertions_passed=True, heal_attempts=0, nothing yet diagnosed), so
    each test overrides only the 1-2 fields its scenario actually cares
    about instead of repeating all 20-odd keys inline. Mirrors
    routers/runs.py's `_blank_state`, which is the real shape every one of
    these fields is initialized to in production.

    Used by test_routing.py, test_serialization.py, test_regressions.py.
    """

    def _make(**overrides) -> HealState:
        state: HealState = {
            "run_id": "test-run-id",
            "source_path": "data/sources/day1_clean.csv",
            "assertions_passed": True,
            "assertion_failures": [],
            "rows_in": 50,
            "rows_out": 50,
            "actual_schema": {},
            "expected_schema": {},
            "drift_items": [],
            "drift_class": None,
            "diagnosis": None,
            "confidence": None,
            "proposed_patch": None,
            "patch_rationale": None,
            "applied_patch": None,
            "awaiting_approval": False,
            "human_decision": None,
            "human_note": None,
            "heal_attempts": 0,
            "status": "running",
            "final_message": None,
            "history": [],
            "assertion_failure_details": [],
            "source_profile": {},
            "specialist_output": None,
        }
        state.update(overrides)
        return state

    return _make


@pytest.fixture
def make_snapshot():
    """Factory for a fake LangGraph StateSnapshot exposing only the 3
    attributes routers/runs.py's `_serialize_state` actually reads
    (.values, .tasks, .next) -- verified against a real compiled graph's
    real get_state() output (see the interrupt/resume probe this test suite
    was built against) rather than guessed. Lets test_serialization.py pin
    the API-shape contract without a real checkpointer or a real
    interrupted run.

    Used by test_serialization.py, test_regressions.py.
    """

    def _make(values: dict, interrupted: bool = False, interrupt_value=None, next=()) -> SimpleNamespace:
        if interrupted:
            tasks = (SimpleNamespace(interrupts=(SimpleNamespace(value=interrupt_value or {}),)),)
        else:
            tasks = ()
        return SimpleNamespace(values=values, tasks=tasks, next=tuple(next))

    return _make


@pytest.fixture
def make_graph_app(make_snapshot):
    """Factory for a fake compiled graph exposing only the one async method
    services/chat_context.py's build_run_context and
    services/analytics.py's per-run summarization actually call:
    `aget_state(config)`, keyed by thread_id (== run_id everywhere in this
    app). Snapshots not present in the given mapping fall back to an empty
    (not-found) snapshot, matching a real checkpointer's response for an
    unknown thread_id.

    Used by test_chat_context.py, test_regressions.py.
    """

    def _make(snapshots_by_run_id: dict):
        class _FakeGraphApp:
            async def aget_state(self, config):
                run_id = config["configurable"]["thread_id"]
                return snapshots_by_run_id.get(run_id, make_snapshot({}))

        return _FakeGraphApp()

    return _make


@pytest.fixture
def make_run_summary():
    """Factory for a dict matching services/analytics.py's
    `_summarize_one` output shape -- the per-run record every
    /api/analytics/* endpoint (routers/analytics.py) aggregates over.
    Building this shape directly, rather than going through a real
    checkpointer, is what lets test_analytics.py and test_regressions.py
    pin the AGGREGATION MATH in routers/analytics.py without any Postgres
    or LangGraph dependency at all -- every endpoint there is a plain
    function that takes collect_run_summaries' output and does arithmetic
    on it.

    Used by test_analytics.py, test_regressions.py.
    """

    def _make(
        run_id: str = "run-1",
        source_path: str = "data/sources/day1_clean.csv",
        status: str = "healed",
        drift_class: str | None = None,
        heal_attempts: int = 0,
        final_confidence: float | None = None,
        approved_by: str | None = None,
        human_decision: str | None = None,
        history: list[dict] | None = None,
        created_at=None,
    ) -> dict:
        from pathlib import PurePosixPath

        return {
            "run_id": run_id,
            "source_path": source_path,
            "dataset": PurePosixPath(source_path).name,
            "status": status,
            "drift_class": drift_class,
            "heal_attempts": heal_attempts,
            "final_confidence": final_confidence,
            "approved_by": approved_by,
            "duration_ms": 1000,
            "created_at": created_at,
            "history": history if history is not None else [],
            "human_decision": human_decision,
        }

    return _make


# ── Stub graph: real topology + real routing, fake node bodies ─────────────
# graph/build.py binds each node as a module-level name
# (`from graph.nodes.apply_patch import apply_patch`) resolved at
# build_graph()'s CALL time against graph.build's own globals -- so
# monkeypatching those names before calling the real build_graph() swaps in
# stub bodies while every add_node/add_conditional_edges/add_edge call, and
# every router in graph/routing.py, still runs for real. This is what lets
# test_graph_structure.py prove the actual topology module works (cycle
# behavior, the history reducer, the interrupt fork) without touching an
# LLM or Postgres, and without hand-duplicating the topology in a second,
# driftable copy.
#
# human_approval, escalate, and commit_report are NOT stubbed: none of the
# three touch an LLM or a database (interrupt() is a pure graph mechanism;
# the other two are plain dict logic), so there is no reason to fake them --
# running them for real is both safe and more meaningful.
_STUB_NAMES = (
    "run_pipeline",
    "profile_source",
    "diff_contract",
    "diagnose",
    "spec_rename",
    "spec_type",
    "spec_nullability",
    "propose_patch",
    "apply_patch",
    "rerun_validate",
)


def _default_stub_bodies() -> dict:
    """One heal-and-done pass by default: drift detected, classified as a
    high-confidence rename, one specialist attempt, immediate pass on
    rerun. Individual tests override just the node(s) their scenario needs
    (typically `rerun_validate`, to control how many loops occur, or
    `diagnose`, to route through human_approval instead of straight to
    apply_patch).
    """

    def run_pipeline(state):
        return {
            "assertions_passed": False,
            "assertion_failures": ["stub: drift detected"],
            "assertion_failure_details": [],
            "rows_in": 10,
            "rows_out": 10,
        }

    def profile_source(state):
        return {"actual_schema": {"col": "str"}, "source_profile": {}}

    def diff_contract(state):
        return {
            "expected_schema": {},
            "drift_items": [
                {
                    "kind": "dtype_mismatch",
                    "column": "col",
                    "expected": "int64",
                    "actual": "str",
                    "example_values": [],
                }
            ],
        }

    def diagnose(state):
        return {"drift_class": "rename", "diagnosis": "stub: looks like a rename", "confidence": 0.9}

    def specialist(state):
        return {"specialist_output": {}}

    def propose_patch(state):
        return {"proposed_patch": {"renames": {"stub": "col"}}, "patch_rationale": "stub rationale"}

    def apply_patch(state):
        # Mirrors the real node's one non-negotiable contract: heal_attempts
        # increments HERE, exactly once per visit -- everything downstream
        # (route_after_rerun's cap check) depends on this being real.
        return {
            "heal_attempts": state["heal_attempts"] + 1,
            "applied_patch": state.get("proposed_patch") or {},
            "awaiting_approval": False,
        }

    def rerun_validate(state):
        attempt = {
            "attempt_no": state["heal_attempts"],
            "drift_summary": state.get("diagnosis") or "",
            "specialist": f"spec_{state.get('drift_class')}",
            "patch": state.get("applied_patch") or {},
            "confidence": state.get("confidence") or 0.0,
            "approved_by": "human" if state.get("human_decision") else "auto",
            "result": "passed",
            "assertion_failures": [],
        }
        return {
            "assertions_passed": True,
            "assertion_failures": [],
            "assertion_failure_details": [],
            "rows_out": 10,
            "history": [attempt],
        }

    return {
        "run_pipeline": run_pipeline,
        "profile_source": profile_source,
        "diff_contract": diff_contract,
        "diagnose": diagnose,
        "spec_rename": specialist,
        "spec_type": specialist,
        "spec_nullability": specialist,
        "propose_patch": propose_patch,
        "apply_patch": apply_patch,
        "rerun_validate": rerun_validate,
    }


@pytest.fixture
def build_stub_graph(monkeypatch):
    """Factory: build_stub_graph(overrides=None, checkpointer=None) ->
    compiled graph. `overrides` replaces any of the 10 stubbable node
    bodies by name; everything else falls back to the deterministic
    default above. All stub bodies are plain sync functions (matching
    human_approval/escalate/commit_report, which already are), so the
    resulting graph has zero async nodes and can be driven with the plain
    sync .invoke()/.stream()/.get_state() API -- verified directly against
    a real compiled graph before writing this suite (interrupt pauses
    without raising; Command(resume=...) continues at the interrupted
    node; operator.add genuinely accumulates history across the resume).

    Used by test_graph_structure.py, test_regressions.py.
    """
    import graph.build as build_module

    def _build(overrides: dict | None = None, checkpointer=None):
        stubs = {**_default_stub_bodies(), **(overrides or {})}
        for name in _STUB_NAMES:
            monkeypatch.setattr(build_module, name, stubs[name])
        return build_module.build_graph(checkpointer or InMemorySaver())

    return _build
