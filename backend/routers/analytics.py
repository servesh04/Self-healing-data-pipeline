"""Read-only analytics endpoints for the dashboard overview (DASHBOARD.md).

HARD BOUNDARY, honored: no graph/pipeline/prompt edits, no new tables, no
writes. Every route here is a GET that aggregates existing data — see
services/analytics.py's docstring for why the aggregation itself goes
through the checkpointer rather than raw SQL for anything touching
per-attempt history.
"""

from fastapi import APIRouter, Depends, Query, Request

from auth import require_token
from services.analytics import collect_run_summaries

router = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(require_token)])

DRIFT_CLASSES = ["rename", "type", "nullability", "unknown"]
SPECIALISTS = ["spec_rename", "spec_type", "spec_nullability"]


def _graph_app(request: Request):
    return request.app.state.graph_app


@router.get("/summary")
async def summary(
    graph_app=Depends(_graph_app), pipeline_env: str | None = Query(None)
) -> dict:
    runs = await collect_run_summaries(graph_app, pipeline_env)
    total = len(runs)
    clean = sum(1 for r in runs if r["status"] == "clean")
    drifted = total - clean  # clean runs never needed healing — excluded per spec
    auto_healed = sum(1 for r in runs if r["status"] == "healed" and r["approved_by"] != "human")
    human_approved = sum(1 for r in runs if r["status"] == "healed" and r["approved_by"] == "human")
    escalated = sum(1 for r in runs if r["status"] == "escalated")

    # Denominator is runs that actually produced ≥1 heal attempt -- NOT
    # drifted_runs. A successful heal is >=1 attempt by definition, so
    # averaging attempts over drifted_runs (which also includes day5's
    # immediate unknown-classified escalations and rejected day6 runs --
    # both 0 attempts, by construction, never having reached a specialist)
    # drags the mean below 1 and makes it read as nonsensical rather than
    # "most drifts heal in one pass, combos take two". Those genuinely
    # never-attempted runs are exactly what auto_heal_rate/escalated already
    # communicate; this metric answers a different question ("given a
    # patch attempt happened, how many did it take") and needs its own
    # denominator to answer it honestly.
    attempted = [r["heal_attempts"] for r in runs if r["heal_attempts"] > 0]
    mean_heal_attempts = sum(attempted) / len(attempted) if attempted else 0.0

    return {
        "total_runs": total,
        "drifted_runs": drifted,
        "auto_healed": auto_healed,
        "human_approved": human_approved,
        "escalated": escalated,
        "auto_heal_rate": round(auto_healed / drifted, 3) if drifted else 0.0,
        "mean_heal_attempts": round(mean_heal_attempts, 2),
        "attempted_runs": len(attempted),  # the mean's actual denominator -- surfaced so it's never a mystery number
        "clean_runs": clean,
    }


@router.get("/confidence")
async def confidence(
    graph_app=Depends(_graph_app), pipeline_env: str | None = Query(None)
) -> list[dict]:
    runs = await collect_run_summaries(graph_app, pipeline_env)
    rows = []
    for r in runs:
        for h in r["history"]:
            rows.append(
                {
                    "run_id": r["run_id"],
                    "attempt_no": h["attempt_no"],
                    "confidence": h["confidence"],
                    "drift_class": h["specialist"].replace("spec_", ""),
                    "specialist": h["specialist"],
                    "result": h["result"],
                    "approved_by": h.get("approved_by"),
                    "kind": "attempt",
                }
            )
        if r["status"] == "escalated" and not r["history"]:
            # A run can escalate with ZERO HealAttempts two different ways:
            # diagnose never found a viable class at all (unknown -- no
            # patch was ever on the table), or a human rejected a real
            # proposed patch (reject skips apply_patch/rerun_validate
            # entirely, so no HealAttempt is ever appended either). Both
            # were previously invisible on this chart, which under-sold
            # the exact point the calibration chart exists to make: low
            # confidence correlates with escalation. diagnose's own
            # confidence/drift_class are accurate here -- nothing
            # overwrites them after an unknown-classified or rejected
            # escalate (see services/analytics.py).
            rejected = r.get("human_decision") == "reject"
            rows.append(
                {
                    "run_id": r["run_id"],
                    "attempt_no": 0,
                    "confidence": r["final_confidence"],
                    "drift_class": r["drift_class"],
                    "specialist": None,
                    "result": "rejected" if rejected else "unclassified",
                    "approved_by": None,
                    "kind": "rejected" if rejected else "unclassified",
                }
            )
    return rows


@router.get("/drift_distribution")
async def drift_distribution(
    graph_app=Depends(_graph_app), pipeline_env: str | None = Query(None)
) -> list[dict]:
    """Per drift class: how often it showed up, and whether the RUN it
    showed up in ultimately healed or escalated.

    Deliberately credits every class an attempt in a given run by that
    run's overall outcome, not by that individual attempt's own
    rerun_validate result. day4_combo's first (rename) attempt always
    reports result="failed" in `history` -- not because the rename patch
    was wrong, but because a SECOND, unrelated drift (type) was still
    present, so the pipeline as a whole hadn't passed yet. The run still
    heals, in 2 attempts. Grouping that attempt under "escalated" for the
    rename class (an earlier version of this endpoint did exactly that)
    told a false story -- "most renames escalate" -- flatly contradicted
    by the runs table sitting right next to it. A class only counts as
    escalated here if the run it appeared in never healed at all.
    """
    runs = await collect_run_summaries(graph_app, pipeline_env)
    counts = {c: {"count": 0, "healed": 0, "escalated": 0} for c in DRIFT_CLASSES}
    for r in runs:
        # Only terminal runs -- clean has nothing to attribute, and a run
        # still "running"/"awaiting_approval" has an undetermined outcome:
        # crediting its class as "escalated" just because it hasn't healed
        # *yet* is the same class of mistake this endpoint exists to avoid
        # (see the day4_combo reasoning above). Found live: a day6 test run
        # left paused at human_approval, with no history yet, was inflating
        # rename's escalated count by 1 purely because it hadn't been
        # resolved yet, not because it had actually escalated.
        if r["status"] not in ("healed", "escalated"):
            continue
        healed = r["status"] == "healed"
        if not r["history"]:
            # Never reached a specialist -- diagnose's own last write is
            # accurate here, nothing overwrites it after an
            # unknown-classified escalate (see services/analytics.py).
            # Always escalated: a run with no history and status != clean
            # is, by construction, either an unknown-classified escalate
            # or a human rejection -- never "healed".
            cls = r["drift_class"] or "unknown"
            if cls in counts:
                counts[cls]["count"] += 1
                counts[cls]["escalated"] += 1
            continue
        for h in r["history"]:
            cls = h["specialist"].replace("spec_", "")
            if cls not in counts:
                continue
            counts[cls]["count"] += 1
            counts[cls]["healed" if healed else "escalated"] += 1
    return [{"drift_class": c, **v} for c, v in counts.items()]


@router.get("/specialist_performance")
async def specialist_performance(
    graph_app=Depends(_graph_app), pipeline_env: str | None = Query(None)
) -> list[dict]:
    runs = await collect_run_summaries(graph_app, pipeline_env)
    tally = {s: {"attempts": 0, "passed": 0} for s in SPECIALISTS}
    for r in runs:
        for h in r["history"]:
            s = h["specialist"]
            if s in tally:
                tally[s]["attempts"] += 1
                if h["result"] == "passed":
                    tally[s]["passed"] += 1
    return [
        {
            "specialist": s,
            "attempts": v["attempts"],
            "passed": v["passed"],
            # None (not 0) for a specialist with zero attempts -- the
            # frontend renders that as "no data", not a misleading 0%.
            "success_rate": round(v["passed"] / v["attempts"], 3) if v["attempts"] else None,
        }
        for s, v in tally.items()
    ]
