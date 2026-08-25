"""Context assembly for the read-only run-data chat (new feature — nothing
here is part of the healing graph; see backend/routers/chat.py's module
docstring for the full boundary this respects).

No knowledge graph, no vector store, no embeddings — total data volume
(~10-15k tokens for the global scope, ~800 for a single run) fits entirely
in context. Retrieval would add a dependency to solve a problem that does
not exist here, and would actively make some answers worse: "why do renames
escalate more than types?" needs every run, not the top-k similar ones.

PER-RUN context (build_run_context, below) is fetched fresh from the
checkpointer for THIS ONE run on every call — deliberately NOT through
services/analytics.py's collect_run_summaries, whose cache has up to a 60s
staleness window (fine, by design, for the dashboard's aggregate views, but
wrong for a run someone may have just triggered and is now asking about).
One aget_state call is cheap; there is no reason to pay the cost of walking
every run in the namespace to answer a question about a single one of them.

GLOBAL context (added once per-run scope has shipped and been verified)
reuses services/analytics.py's cached aggregates directly — precomputed
figures are the entire anti-hallucination strategy for counting questions,
and that cache is exactly what makes reusing them cheap.
"""

import json
import logging
from pathlib import Path

from services.analytics import collect_run_summaries

log = logging.getLogger(__name__)

# Cap + budget knobs. No tokenizer dependency for a check that only needs to
# be in the right ballpark — ~4 chars/token is the standard rule-of-thumb
# estimate for English/JSON text, good enough to catch "this got way too
# big" without adding tiktoken as a dependency for one log line.
MAX_CONTEXT_TOKENS = 30_000
PATCH_TRUNCATE_CHARS = 500
MAX_ASSERTION_FAILURES_PER_ATTEMPT = 3


def estimate_tokens(payload) -> int:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return len(text) // 4


def _truncate_patch(patch: dict | None) -> str:
    text = json.dumps(patch or {})
    if len(text) <= PATCH_TRUNCATE_CHARS:
        return text
    return text[:PATCH_TRUNCATE_CHARS] + f"...(truncated, {len(text)} chars total)"


def _truncate_failures(failures: list[str] | None) -> list[str]:
    failures = list(failures or [])
    if len(failures) <= MAX_ASSERTION_FAILURES_PER_ATTEMPT:
        return failures
    shown = failures[:MAX_ASSERTION_FAILURES_PER_ATTEMPT]
    shown.append(f"+{len(failures) - MAX_ASSERTION_FAILURES_PER_ATTEMPT} more")
    return shown


def _condense_history(history: list[dict]) -> list[dict]:
    return [
        {
            "attempt_no": h.get("attempt_no"),
            "specialist": h.get("specialist"),
            "patch": _truncate_patch(h.get("patch")),
            "confidence": h.get("confidence"),
            "result": h.get("result"),
            "approved_by": h.get("approved_by"),
            "assertion_failures": _truncate_failures(h.get("assertion_failures")),
        }
        for h in history
    ]


async def build_run_context(run_id: str, graph_app) -> dict | None:
    """Everything the chat needs to answer questions about ONE run —
    run_id, dataset, status, drift_class, diagnosis, confidence,
    heal_attempts, approved_by, human_note, assertion_failures, drift_items,
    and the full (truncated) history list. Returns None if the run doesn't
    exist (caller returns 404).
    """
    snapshot = await graph_app.aget_state({"configurable": {"thread_id": run_id}})
    if not snapshot.values:
        return None

    values = snapshot.values
    # Live-interrupt check — the same 2 lines used in services/analytics.py
    # and routers/runs.py's _serialize_state, duplicated rather than
    # imported: this module must not import from routers/ (chat.py is a
    # router; a router importing a router is the wrong direction), and it's
    # too small to justify a new shared dependency between them.
    interrupts = [i.value for task in snapshot.tasks for i in task.interrupts]
    status = "awaiting_approval" if interrupts else values.get("status", "running")

    history = values.get("history") or []
    last = history[-1] if history else None
    approved_by = last.get("approved_by") if last else None

    context = {
        "run_id": run_id,
        "dataset": Path(values.get("source_path") or "").name,
        "status": status,
        "drift_class": values.get("drift_class"),
        "diagnosis": values.get("diagnosis"),
        "confidence": values.get("confidence"),
        "heal_attempts": values.get("heal_attempts", 0),
        "approved_by": approved_by,
        "human_decision": values.get("human_decision"),
        "human_note": values.get("human_note"),
        "assertion_failures": _truncate_failures(values.get("assertion_failures")),
        "drift_items": values.get("drift_items") or [],
        "history": _condense_history(history),
        # human_decision, proposed_patch, applied_patch, patch_rationale, and
        # final_message are not in the field list this feature was speced
        # with -- added after testing against a real rejected day6 run
        # exposed why they're necessary, not optional: a REJECTED run's
        # `history` is empty (reject skips apply_patch/rerun_validate
        # entirely, so no HealAttempt is ever appended -- same fact
        # DASHBOARD.md's confidence-chart fix already relied on), so without
        # these fields the model had no way to know a human ever reviewed
        # anything, only that confidence was low and the run ended
        # escalated -- a materially incomplete answer, not a wrong one, but
        # incomplete in exactly the way this feature exists to prevent.
        # final_message in particular is escalate.py's own plain-language
        # reason string (e.g. "escalated -- human rejected the proposed
        # patch") -- the single most direct answer to "why did this
        # escalate?" available anywhere in state.
        "proposed_patch": values.get("proposed_patch"),
        "applied_patch": values.get("applied_patch"),
        "patch_rationale": values.get("patch_rationale"),
        "final_message": values.get("final_message"),
    }

    tokens = estimate_tokens(context)
    if tokens > MAX_CONTEXT_TOKENS:
        log.warning(
            "chat_context: per-run context for %s estimated at %d tokens (cap %d)",
            run_id, tokens, MAX_CONTEXT_TOKENS,
        )
    return context


# ── Global scope ─────────────────────────────────────────────────────────
# Shipped after per-run was built, verified against real seeded data AND
# the deployed backend (explicit build order for this feature).
#
# Reuses routers.analytics's summary/drift_distribution/
# specialist_performance/confidence functions directly (called with an
# explicit graph_app= argument — they're plain async functions underneath
# the @router.get decorator, so Depends(...)'s sentinel default is simply
# overridden) rather than recomputing anything. This is a service importing
# from a router, backwards from the usual layering — accepted deliberately
# rather than moving that aggregation logic to avoid touching routers/
# analytics.py at all, which is outside this feature's "new files only"
# boundary. The alternative (reimplementing the same aggregation here) would
# create a second place for the exact counting bugs this feature exists to
# prevent to diverge from the dashboard's own numbers.
from routers.analytics import (  # noqa: E402
    confidence as analytics_confidence,
    drift_distribution as analytics_drift_distribution,
    specialist_performance as analytics_specialist_performance,
    summary as analytics_summary,
)


def _lean_run(r: dict) -> dict:
    created_at = r.get("created_at")
    return {
        "run_id": r["run_id"],
        "dataset": r["dataset"],
        "status": r["status"],
        "drift_class": r["drift_class"],
        "heal_attempts": r["heal_attempts"],
        "final_confidence": r["final_confidence"],
        "approved_by": r["approved_by"],
        "duration_ms": r["duration_ms"],
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
    }


async def build_global_context(
    graph_app,
    pipeline_env: str | None = None,
    status: str | None = None,
    dataset: str | None = None,
) -> dict:
    """Aggregates ALWAYS included in full — they're the anti-hallucination
    strategy for every counting/rate question. Per-run detail (the `runs`
    list) is a LEAN projection (no history — that granularity already lives
    in confidence_rows), and filterable by status/dataset so a narrower
    question doesn't need every run's detail to answer honestly.

    This design breaks down somewhere around 200+ runs (context stops
    fitting comfortably even at aggregates + lean records). The fix at that
    point is filtering per-run detail more aggressively while keeping
    aggregates complete -- not retrieval. Not built now; this repo's seeded
    dataset is 20 runs.
    """
    summary = await analytics_summary(graph_app=graph_app, pipeline_env=pipeline_env)
    drift_dist = await analytics_drift_distribution(graph_app=graph_app, pipeline_env=pipeline_env)
    spec_perf = await analytics_specialist_performance(graph_app=graph_app, pipeline_env=pipeline_env)
    conf_rows = await analytics_confidence(graph_app=graph_app, pipeline_env=pipeline_env)

    runs = await collect_run_summaries(graph_app, pipeline_env)
    if status:
        runs = [r for r in runs if r["status"] == status]
    if dataset:
        runs = [r for r in runs if r["dataset"] == dataset]
    runs.sort(key=lambda r: r["created_at"] or "", reverse=True)

    context = {
        "summary": summary,
        "drift_distribution": drift_dist,
        "specialist_performance": spec_perf,
        "confidence_rows": conf_rows,
        "runs": [_lean_run(r) for r in runs],
    }

    tokens = estimate_tokens(context)
    if tokens > MAX_CONTEXT_TOKENS:
        log.warning(
            "chat_context: global context estimated at %d tokens (cap %d)",
            tokens, MAX_CONTEXT_TOKENS,
        )
    return context
