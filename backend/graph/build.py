"""Wires HealState + all nodes into the topology from ARCHITECTURE.md's
"Graph Topology" section. Node bodies are stubs until Phase 3; the topology
itself — the graded artifact — is real from this phase on.

`build_graph` takes a checkpointer rather than constructing one, so Phase 2
can compile against an in-memory checkpointer (scripts/graph_smoke_test.py)
and Phase 4 can compile the identical topology against the Postgres one
(backend/db.py's pool wrapped in AsyncPostgresSaver) without touching this
file.
"""

from langgraph.graph import END, START, StateGraph

from graph.nodes.apply_patch import apply_patch
from graph.nodes.commit_report import commit_report
from graph.nodes.diagnose import diagnose
from graph.nodes.diff_contract import diff_contract
from graph.nodes.escalate import escalate
from graph.nodes.human_approval import human_approval
from graph.nodes.profile_source import profile_source
from graph.nodes.propose_patch import propose_patch
from graph.nodes.rerun_validate import rerun_validate
from graph.nodes.run_pipeline import run_pipeline
from graph.nodes.spec_nullability import spec_nullability
from graph.nodes.spec_rename import spec_rename
from graph.nodes.spec_type import spec_type
from graph.routing import (
    route_after_confidence,
    route_after_diagnose,
    route_after_human_approval,
    route_after_rerun,
    route_after_run,
)
from graph.state import HealState


def build_graph(checkpointer):
    g = StateGraph(HealState)

    g.add_node("run_pipeline", run_pipeline)
    g.add_node("profile_source", profile_source)
    g.add_node("diff_contract", diff_contract)
    g.add_node("diagnose", diagnose)
    g.add_node("spec_rename", spec_rename)
    g.add_node("spec_type", spec_type)
    g.add_node("spec_nullability", spec_nullability)
    g.add_node("propose_patch", propose_patch)
    g.add_node("human_approval", human_approval)
    g.add_node("apply_patch", apply_patch)
    g.add_node("rerun_validate", rerun_validate)
    g.add_node("escalate", escalate)
    g.add_node("commit_report", commit_report)

    g.add_edge(START, "run_pipeline")

    g.add_conditional_edges(
        "run_pipeline",
        route_after_run,
        {"commit_report": "commit_report", "profile_source": "profile_source"},
    )
    g.add_edge("profile_source", "diff_contract")
    g.add_edge("diff_contract", "diagnose")

    g.add_conditional_edges(
        "diagnose",
        route_after_diagnose,
        {
            "spec_rename": "spec_rename",
            "spec_type": "spec_type",
            "spec_nullability": "spec_nullability",
            "escalate": "escalate",
        },
    )
    g.add_edge("spec_rename", "propose_patch")
    g.add_edge("spec_type", "propose_patch")
    g.add_edge("spec_nullability", "propose_patch")

    g.add_conditional_edges(
        "propose_patch",
        route_after_confidence,
        {"apply_patch": "apply_patch", "human_approval": "human_approval"},
    )
    g.add_conditional_edges(
        "human_approval",
        route_after_human_approval,
        {"apply_patch": "apply_patch", "escalate": "escalate"},
    )
    g.add_edge("apply_patch", "rerun_validate")

    g.add_conditional_edges(
        "rerun_validate",
        route_after_rerun,
        {
            "commit_report": "commit_report",
            "diff_contract": "diff_contract",  # ← THE HEAL CYCLE
            "escalate": "escalate",
        },
    )

    g.add_edge("commit_report", END)
    g.add_edge("escalate", END)

    return g.compile(checkpointer=checkpointer)
