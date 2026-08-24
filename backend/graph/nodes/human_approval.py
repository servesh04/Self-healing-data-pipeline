"""`interrupt()` — halts the graph, persists state to the checkpointer.

Real from Phase 2 on: the halt/resume mechanic has nothing to do with LLMs,
so there is no reason to fake it. What Phase 4 adds is the Postgres
checkpointer (so it survives a process restart) and the HTTP approve/reject
endpoints that call `Command(resume=...)`. Phase 2 runs this against an
in-memory checkpointer — see backend/graph/build.py — which is sufficient to
prove the interrupt actually halts and actually resumes.

Must have NO side effects before the `interrupt()` call: LangGraph
re-executes this function from the top on every resume (verified in Phase 0
by running a real two-attempt cycle), so anything placed before it would
double-fire on resume.
"""

from langgraph.types import interrupt


def human_approval(state: dict) -> dict:
    decision = interrupt(
        {
            "proposed_patch": state.get("proposed_patch"),
            "patch_rationale": state.get("patch_rationale"),
            "confidence": state.get("confidence"),
        }
    )
    # Command(resume=...) can carry either a bare "approve"/"reject" string
    # (Phase 2 smoke test) or a {"decision": ..., "note": ...} dict (what the
    # Phase 4 HTTP endpoints will actually send). Accept both.
    if isinstance(decision, dict):
        return {
            "human_decision": decision.get("decision"),
            "human_note": decision.get("note"),
            "awaiting_approval": False,
        }
    return {
        "human_decision": decision,
        "human_note": None,
        "awaiting_approval": False,
    }
